#!/usr/bin/env python3
"""
TSE 51 BEST scanner -> Telegram signals + Design A / Design B records for the MT5 executor.

One run = one scan of every pair on the last COMPLETED M30 bar, then exit.
  * candles come from the BROKER feed: the EA in the Hudsonventura container exports
    tse_candles_<PAIR>.csv (KB_CANDLE_DIR); Twelve Data / the old bridge are optional fallbacks
  * every signal is sent to Telegram; only pairs in KB_EXEC_PAIRS are handed to the executor
  * Design A: SL = 2 x ATR(M30), no TP, closed after horizon_bars
  * Design B: SL = 2 x ATR(M30), TP = the strategy's research net return converted to price
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests

from config import (
    DEFAULT_PAIRS, EXEC_PAIRS, ENABLED_DESIGNS, HORIZON_BARS, AVG_RESEARCH_NET,
    SL_ATR_MULT, ATR_PERIOD, COOLDOWN_MINUTES, STATE_DIR, EXEC_TIMEFRAME, MAX_BAR_AGE_MIN,
)
from states.market_states import atr_wilder
from states.strategy_eval import evaluate_strategies, load_best51
from states.thresholds import get_thresholds
from utils.time_utils import is_active_session
from utils.logger import get_logger
from utils.trade_logger import log_signal

logger = get_logger("scan_once")

COOLDOWN_FILE = os.path.join(STATE_DIR, "cooldown.json")
SIGNALS_OUT = "signals_out.json"          # signals for the executor (exec pairs only)
BRIDGE = os.environ.get("MT5_API_URL", "http://127.0.0.1:5000/api")
SUFFIX = os.environ.get("SYMBOL_SUFFIX", "")


# -- small helpers -----------------------------------------------------------
def load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def send_telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.warning("Telegram not configured; signal not sent")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            logger.error("Telegram HTTP %s", r.status_code)
    except requests.RequestException as e:
        logger.error("Telegram error: %s", type(e).__name__)


# -- data --------------------------------------------------------------------
def _td_symbol(pair: str) -> str:
    return f"{pair[:3]}/{pair[3:]}"          # EURUSD -> EUR/USD, XAUUSD -> XAU/USD


def fetch_m30_twelvedata(pair: str, limit: int, key: str) -> pd.DataFrame:
    """Last `limit` M30 bars from Twelve Data in UTC; the still-forming bar is dropped."""
    params = {"symbol": _td_symbol(pair), "interval": "30min", "outputsize": limit,
              "timezone": "UTC", "order": "ASC", "apikey": key}
    body = None
    for attempt in (1, 2):
        r = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
        body = r.json()
        if body.get("code") == 429 and attempt == 1:       # per-minute credit limit: wait, retry once
            time.sleep(20)
            continue
        break
    if r.status_code != 200 or body.get("status") == "error" or not body.get("values"):
        raise RuntimeError(f"twelvedata {r.status_code}: {body.get('code')} {body.get('message')}")
    df = pd.DataFrame(body["values"])
    df["timestamp"] = pd.to_datetime(df["datetime"], utc=True)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)
    now = pd.Timestamp.now(tz="UTC")
    if df["timestamp"].iloc[-1] > now:
        raise RuntimeError(f"twelvedata bar timestamp {df['timestamp'].iloc[-1]} is in the future: timezone mismatch")
    closed = df["timestamp"] + pd.Timedelta(minutes=30) <= now      # keep completed bars only
    return df[closed][["timestamp", "open", "high", "low", "close"]].reset_index(drop=True)


def fetch_m30_files(pair: str, limit: int, folder: str) -> pd.DataFrame:
    """Completed M30 bars exported by the EA (UTC epoch seconds); never falls back silently."""
    path = os.path.join(folder, f"tse_candles_{pair}.csv")
    if not os.path.exists(path):
        raise RuntimeError(f"broker candle file missing: {path}")
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["time_utc"], unit="s", utc=True)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        raise RuntimeError(f"broker candle file empty: {path}")
    now = pd.Timestamp.now(tz="UTC")
    newest_close = df["timestamp"].iloc[-1] + pd.Timedelta(minutes=30)
    if newest_close > now + pd.Timedelta(minutes=2):
        raise RuntimeError(f"{pair}: newest bar closes {newest_close}, in the future: server-time offset is wrong")
    return df[["timestamp", "open", "high", "low", "close"]].tail(limit).reset_index(drop=True)


def fetch_m30(pair: str, limit: int = 400) -> pd.DataFrame:
    """Last `limit` completed M30 bars: broker files (KB_CANDLE_DIR) > Twelve Data key > bridge."""
    symbol = pair + SUFFIX
    local = os.environ.get("KB_M30_CSV")          # local testing only
    if local:
        df = pd.read_csv(local, sep=None, engine="python")
        df.columns = [c.strip().lower() for c in df.columns]
        if "timestamp_utc" in df.columns:
            df = df.rename(columns={"timestamp_utc": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").tail(limit).reset_index(drop=True)

    candle_dir = os.environ.get("KB_CANDLE_DIR")
    if candle_dir:
        return fetch_m30_files(pair, limit, candle_dir)

    td_key = os.environ.get("TWELVE_DATA_API_KEY")
    if td_key:
        return fetch_m30_twelvedata(pair, limit, td_key)

    r = requests.get(f"{BRIDGE}/copy_rates_from_pos",
                     params={"symbol": symbol, "timeframe": EXEC_TIMEFRAME, "start_pos": 0, "count": limit},
                     headers={"x-api-key": os.environ.get("API_KEY", "")}, timeout=30)
    body = r.json()
    if r.status_code != 200 or body.get("code") != 200:
        raise RuntimeError(f"bridge {r.status_code}: {body.get('message')} {body.get('details')}")
    rows = body.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("rates") or rows.get("data") or []
    if not rows:
        raise RuntimeError("bridge returned no candles")
    df = pd.DataFrame(rows)
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={c: "timestamp" for c in df.columns if c in ("time", "datetime", "timestamp")})
    if pd.api.types.is_numeric_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)
    return df.iloc[:-1].reset_index(drop=True)      # drop the forming bar


def bar_is_fresh(df: pd.DataFrame) -> bool:
    """False when the last closed bar is old (weekend, holiday, frozen feed)."""
    last_open = df["timestamp"].iloc[-1]
    close_time = last_open + pd.Timedelta(minutes=30)
    age_min = (datetime.now(timezone.utc) - close_time.to_pydatetime()).total_seconds() / 60
    return age_min <= MAX_BAR_AGE_MIN


# -- signals -----------------------------------------------------------------
def build_records(pair: str, df: pd.DataFrame, hit: Dict, executed: bool, calibrated: bool) -> List[Dict]:
    i = len(df) - 1
    a = float(atr_wilder(df, ATR_PERIOD)[i])
    if not np.isfinite(a) or a <= 0:
        return []
    entry = float(df["close"].iloc[i])
    side = hit["side"]
    sl_dist = SL_ATR_MULT * a
    ts = df["timestamp"].iloc[i].to_pydatetime()
    bar_tag = ts.strftime("%Y%m%d%H%M")
    horizon = int(hit.get("horizon_bars") or HORIZON_BARS)
    oos = float(hit.get("oos_net") or 0)
    net = oos if oos > 0 else AVG_RESEARCH_NET
    rank = int(hit.get("rank") or 0)

    records = []
    for design in ENABLED_DESIGNS:
        if design == "A":
            tp_dist, mode = 0.0, "RESEARCH"
        elif design == "B":
            # research "net" is a RETURN (0.0013 = 0.13%). Convert to price: x entry.
            tp_dist, mode = net * entry, "HYBRID"
        else:
            continue
        sl = entry - sl_dist if side == "BUY" else entry + sl_dist
        tp = (entry + tp_dist if side == "BUY" else entry - tp_dist) if tp_dist > 0 else 0.0
        records.append({
            "signal_id": f"TSE-{design}-{pair}-{bar_tag}-R{rank:02d}"[:31],
            "pair": pair, "side": side,
            "entry": round(entry, 5), "sl": round(sl, 5), "tp": round(tp, 5),
            "sl_dist": round(sl_dist, 6), "tp_dist": round(tp_dist, 6),
            "atr": round(a, 6), "atr_timeframe": EXEC_TIMEFRAME,
            "design": design, "mode": mode,
            "pattern": hit.get("pattern", f"R{rank:02d}"), "strategy_id": hit.get("strategy_id", ""),
            "strategy_rank": rank, "plain_a": hit.get("plain_a", ""), "plain_b": hit.get("plain_b", ""),
            "horizon_bars": horizon, "research_net": oos,
            "bar_time": ts.isoformat(), "signal_ts": int(time.time()),
            "hourly_atr_mult": 1.0, "hourly_atr": round(a, 6),
            "executed": executed, "calibrated": calibrated,
        })
    return records


def telegram_text(pair: str, recs: List[Dict]) -> str:
    r = recs[0]
    icon = "🟢" if r["side"] == "BUY" else "🔴"
    lines = [f"<b>{icon} {pair} {r['side']}</b>  (M30 bar {r['bar_time'][11:16]} UTC)",
             f"<i>{r['plain_a']}</i>", f"<i>{r['plain_b']}</i>", "",
             f"Entry ~ <code>{r['entry']}</code>   SL <code>{r['sl']}</code> ({SL_ATR_MULT:g}×ATR)"]
    for x in recs:
        if x["design"] == "A":
            lines.append(f"Design A: no TP, close after {x['horizon_bars']} bars (~{x['horizon_bars'] // 2}h)")
        else:
            lines.append(f"Design B: TP <code>{x['tp']}</code>")
    lines.append(f"Research net {r['research_net']:.5f} | rank {r['strategy_rank']}")
    if not r["calibrated"]:
        lines.append("⚠️ <i>Thresholds are the EURUSD ones (this pair is not calibrated)</i>")
    lines.append("⚙️ <i>Sent to MT5 executor</i>" if r["executed"] else "ℹ️ <i>Signal only (not auto-traded)</i>")
    return "\n".join(lines)


def main() -> int:
    force = os.environ.get("KB_FORCE_SCAN", "false").lower() == "true"
    cooldowns: Dict[str, float] = load_json(COOLDOWN_FILE, {})
    now = time.time()
    exec_records: List[Dict] = []
    n_pairs_ok = n_pairs_err = 0

    n_strats = len(load_best51())
    logger.info("Loaded %d BEST strategies; pairs %s; auto-traded: %s",
                n_strats, ",".join(DEFAULT_PAIRS), ",".join(EXEC_PAIRS))

    if not is_active_session() and not force:
        logger.info("Off-hours; no new-signal scan (open trades are still managed by the executor)")
    else:
        for pair in DEFAULT_PAIRS:
            if now - cooldowns.get(pair, 0) < COOLDOWN_MINUTES * 60:
                logger.info("%s on cooldown", pair)
                continue
            try:
                df = fetch_m30(pair)
            except Exception as exc:
                n_pairs_err += 1
                logger.error("%s: data error: %s", pair, str(exc)[:160])
                continue
            n_pairs_ok += 1
            if len(df) < 120:
                logger.warning("%s: only %d bars; skipped", pair, len(df))
                continue
            if not os.environ.get("KB_M30_CSV") and not bar_is_fresh(df):
                logger.info("%s: last closed bar %s is stale; skipped", pair, df["timestamp"].iloc[-1])
                continue

            _, calibrated = get_thresholds(pair)
            hits = evaluate_strategies(df, pair)
            if not hits:
                logger.info("%s: no BEST strategy agrees on the last bar", pair)
                continue

            best_by_side: Dict[str, Dict] = {}
            for h in sorted(hits, key=lambda x: (-x.get("oos_net", 0), x.get("rank", 99))):
                best_by_side.setdefault(h["side"], h)

            executed = pair in EXEC_PAIRS
            for hit in best_by_side.values():
                recs = build_records(pair, df, hit, executed, calibrated)
                if not recs:
                    continue
                for rec in recs:
                    try:
                        log_signal(rec)
                    except Exception as exc:
                        logger.warning("signal log failed: %s", exc)
                send_telegram(telegram_text(pair, recs))
                cooldowns[pair] = time.time()
                if executed:
                    exec_records.extend(recs)
                logger.info("%s %s rank=%s -> %d design(s) [%s]", pair, hit["side"], hit.get("rank"),
                            len(recs), "executor" if executed else "signal only")

    save_json(COOLDOWN_FILE, cooldowns)
    save_json(SIGNALS_OUT, exec_records)
    logger.info("Scan done: %d executor record(s)", len(exec_records))

    # --- Signal Output Interface (canonical schema for Hudsonventura path) ---
    # Original TSE records remain the source of truth; we only serialise them.
    try:
        from signal_interface.adapter import records_from_scan_once
        from signal_interface.exporter import SignalExporter
        from pathlib import Path

        if exec_records:
            canonical = records_from_scan_once(exec_records)
            out_jsonl = Path(STATE_DIR) / "signals_canonical.jsonl"
            n = SignalExporter().export_jsonl(canonical, out_jsonl)
            logger.info("Signal Output Interface: wrote %d canonical records → %s", n, out_jsonl)
        else:
            logger.info("Signal Output Interface: no executor records to export")
    except Exception as exc:
        # Never fail the original scan because of the interface layer
        logger.warning("Signal Output Interface export failed (non-fatal): %s", exc)

    if n_pairs_err and not n_pairs_ok:       # every pair failed: surface it, do not look "green"
        logger.error("No pair could be scanned (bridge/data problem)")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logger.error("Fatal: %s", e)
        sys.exit(1)
