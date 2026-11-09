#!/usr/bin/env python3
"""
MT5 executor for the TSE strategies (runs inside GitHub Actions, one run per scan).

Uses ONLY endpoints documented by liberatti/mt5bridge:
  GET  /api/terminal_info /api/account_info /api/symbol_info/<s> /api/symbol_info_tick/<s>
  GET  /api/positions_get /api/history_deals_get      POST /api/symbol_select
  POST /api/order/open   /api/order/modify   /api/order/close

Each run:  wait for terminal -> manage open trades -> open new signals.
  manage:  Design A is closed after horizon_bars; both designs get the hourly stop tighten
           (+1 ATR in the trade direction, never past a valid distance from the live price)
  open:    one position per symbol AND design (so A and B can run side by side)

Modes:  python mt5_execute.py --wait     only wait until the terminal is connected, then exit
Env:    DRY_RUN (default TRUE), SIGNALS_JSON or signals_out.json, SYMBOL_SUFFIX, KB_RISK_PERCENT,
        LOT_SIZE (fixed lots, overrides risk sizing), KB_MAX_VOLUME, MAX_OPEN, MAX_AGE_MIN,
        MAX_DRIFT_FRAC, API_KEY, MT5_API_URL, KB_STATE_DIR
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from utils.trade_logger import log_outcome, infer_exit_reason

BASE = os.environ.get("MT5_API_URL", "http://127.0.0.1:5000/api")
HDR = {"x-api-key": os.environ.get("API_KEY", "")}
STATE_DIR = os.environ.get("KB_STATE_DIR", "state")
TRADES_FILE = os.path.join(STATE_DIR, "trades.json")
COOLDOWN_FILE = os.path.join(STATE_DIR, "cooldown.json")
KEEP_CLOSED_SECONDS = 3 * 86400
HOUR_SECS = 3600


# -- plumbing ----------------------------------------------------------------
def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def log(msg: str) -> None:
    print(msg, flush=True)


def is_dry() -> bool:
    return env("DRY_RUN", "true").lower() != "false"      # anything except an explicit "false" is a dry run


def call(method: str, path: str, **kw) -> Any:
    r = requests.request(method, f"{BASE}{path}", headers=HDR, timeout=30, **kw)
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"{path}: non-JSON response (HTTP {r.status_code})")
    if r.status_code != 200 or body.get("code") != 200:
        raise RuntimeError(f"{path}: HTTP {r.status_code} {body.get('message')} {body.get('details')}")
    return body.get("data", body)


def as_list(data) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("positions", "orders", "deals", "rates", "data"):
            if isinstance(data.get(k), list):
                return data[k]
    return [data]


def safe_list(path: str, params: Optional[dict] = None) -> list:
    try:
        return as_list(call("GET", path, params=params))
    except Exception as exc:
        log(f"list {path}: {str(exc)[:120]}")
        return []


def send_telegram(text: str) -> None:
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text[:4000]}, timeout=15)
    except requests.RequestException as exc:
        log(f"telegram error: {type(exc).__name__}")


def load_trades() -> List[Dict]:
    try:
        with open(TRADES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_trades(trades: List[Dict]) -> None:
    now = time.time()
    keep = [t for t in trades
            if t.get("status") == "OPEN" or (t.get("status") == "CLOSED" and now - float(t.get("closed_ts") or now) < KEEP_CLOSED_SECONDS)]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(keep, f, indent=2)


def wait_ready(timeout: int = 300) -> None:
    """Block until the terminal is really connected to the broker and the account is loaded."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            ti = call("GET", "/terminal_info")
            if ti.get("connected"):
                ai = call("GET", "/account_info")
                if ai.get("login"):
                    log("terminal connected, account loaded")
                    return
        except Exception as exc:
            last = exc
        time.sleep(5)
    raise RuntimeError(f"MT5 bridge not ready after {timeout}s ({str(last)[:100]})")


# -- symbol helpers ------------------------------------------------------------
_INFO: Dict[str, dict] = {}


def sym_info(symbol: str) -> dict:
    if symbol not in _INFO:
        try:
            _INFO[symbol] = call("GET", f"/symbol_info/{symbol}") or {}
        except Exception:
            _INFO[symbol] = {}
    return _INFO[symbol]


def tick_size(symbol: str) -> float:
    i = sym_info(symbol)
    return float(i.get("trade_tick_size") or i.get("point") or 0.00001)


def normalize_price(symbol: str, price: float) -> float:
    t = tick_size(symbol)
    return round(round(price / t) * t, 10) if t > 0 else round(price, 5)


def min_stop_gap(symbol: str) -> float:
    """Smallest allowed distance between price and a stop (broker stops level + cushion)."""
    i = sym_info(symbol)
    point = float(i.get("point") or tick_size(symbol))
    return (float(i.get("trade_stops_level") or 0) + 2) * point


def calc_lot(symbol: str, sl_dist: float) -> float:
    """Risk-based lots (KB_RISK_PERCENT of equity per trade); LOT_SIZE overrides with a fixed size."""
    max_vol = float(env("KB_MAX_VOLUME", env("MAX_VOLUME", "0.10")))
    fixed = float(env("LOT_SIZE", "0") or "0")
    if fixed > 0:
        return min(fixed, max_vol)
    risk_pct = float(env("KB_RISK_PERCENT", "1.0") or "1.0")
    try:
        equity = float(call("GET", "/account_info").get("equity") or 0)
        i = sym_info(symbol)
        ts = tick_size(symbol)
        tv = float(i.get("trade_tick_value") or 0)
        vmin = float(i.get("volume_min") or 0.01)
        vstep = float(i.get("volume_step") or 0.01)
        if equity <= 0 or sl_dist <= 0 or ts <= 0 or tv <= 0:
            return vmin
        raw = (equity * risk_pct / 100.0) / ((sl_dist / ts) * tv)
        lot = max(vmin, math.floor(raw / vstep) * vstep)
        return round(min(lot, max_vol), 2)
    except Exception as exc:
        log(f"lot sizing fallback: {str(exc)[:100]}")
        return min(0.01, max_vol)


def find_position(symbol: str, tag: str = "", ticket: Any = None) -> Optional[Dict]:
    for p in safe_list("/positions_get", {"symbol": symbol}):
        if ticket and str(p.get("ticket")) == str(ticket):
            return p
        if tag and str(p.get("comment", "")).startswith(tag):
            return p
    return None


# -- management: reconcile, horizon close, hourly stop tighten ---------------------
def mark_closed(trade: Dict, reason: str, px: float) -> None:
    trade["status"], trade["closed_ts"], trade["exit_price"], trade["close_reason"] = "CLOSED", time.time(), px, reason
    try:
        log_outcome(trade, reason, exit_price=px)
    except Exception as exc:
        log(f"outcome log failed: {exc}")
    send_telegram(f"TSE POSITION CLOSED\n{trade.get('symbol')} {trade.get('side')} | Design {trade.get('design')}\n"
                  f"Ticket {trade.get('ticket')} | {reason}")


def reconcile_missing(trade: Dict) -> None:
    """The position is gone from the terminal: find out how it ended."""
    ticket, now = trade.get("ticket"), time.time()
    if is_dry():
        log(f"[dry-run] {ticket}: position no longer open; ledger left unchanged")
        return
    px, reason = None, None
    try:
        opened = datetime.fromtimestamp(float(trade.get("opened_ts") or now), tz=timezone.utc)
        deals = as_list(call("GET", "/history_deals_get", params={
            "position": ticket, "date_from": opened.isoformat(), "date_to": datetime.now(timezone.utc).isoformat()}))
        closing = [d for d in deals if int(d.get("entry", -1)) in (1, 2, 3) and d.get("price") is not None]
        if closing:
            px = float(closing[-1]["price"])
            reason = infer_exit_reason(trade, px)
    except Exception as exc:
        log(f"{ticket}: history lookup failed: {str(exc)[:100]}")
    if px is None:                                  # history unavailable: conclude after two missing runs
        trade["missing_runs"] = int(trade.get("missing_runs") or 0) + 1
        if trade["missing_runs"] < 2:
            return
        px = float(trade.get("last_price") or trade.get("entry") or 0)
        reason = "UNKNOWN"
    mark_closed(trade, reason or "UNKNOWN", px)


def tighten_stop(trade: Dict, pos: Dict) -> int:
    """+1 ATR in the trade direction, clamped to a valid distance from the live price. Returns failures."""
    symbol, side, ticket = trade["symbol"], trade["side"], trade["ticket"]
    step = float(trade.get("hourly_atr") or trade.get("atr") or 0) * float(trade.get("hourly_atr_mult") or 1.0)
    if step <= 0:
        return 0
    cur_sl = float(pos.get("sl") or 0)
    cur_tp = float(pos.get("tp") or 0)
    if cur_sl <= 0:
        return 0                                    # no stop to tighten
    tick = call("GET", f"/symbol_info_tick/{symbol}")
    bid, ask = float(tick["bid"]), float(tick["ask"])
    gap = min_stop_gap(symbol)

    if side == "BUY":
        new_sl = min(cur_sl + step, bid - gap)      # never above what the broker allows
        improved = new_sl > cur_sl + tick_size(symbol) / 2
    else:
        new_sl = max(cur_sl - step, ask + gap)
        improved = new_sl < cur_sl - tick_size(symbol) / 2
    if not improved:
        log(f"{ticket}: price too close to tighten the stop this hour; will retry")
        return 0
    new_sl = normalize_price(symbol, new_sl)

    if is_dry():
        log(f"[dry-run] would tighten {ticket} SL {cur_sl} -> {new_sl}")
        return 0
    try:
        call("POST", "/order/modify", json={"ticket": int(ticket), "sl": new_sl, "tp": cur_tp})
        after = find_position(symbol, ticket=ticket)
        if after and abs(float(after.get("sl") or 0) - new_sl) <= tick_size(symbol) * 2:
            trade["sl"], trade["last_ratchet_ts"] = new_sl, time.time()
            trade["ratchet_count"] = int(trade.get("ratchet_count") or 0) + 1
            log(f"{ticket}: stop tightened to {new_sl} (count {trade['ratchet_count']})")
            return 0
        log(f"{ticket}: modify sent but new SL not confirmed; CHECK ACCOUNT")
        return 1
    except Exception as exc:
        log(f"{ticket}: SL modify failed: {str(exc)[:140]}")
        return 1


def manage(trades: List[Dict]) -> int:
    failures, now = 0, time.time()
    for trade in trades:
        if trade.get("status") != "OPEN":
            continue
        symbol, ticket, design = trade["symbol"], trade.get("ticket"), str(trade.get("design", "A")).upper()
        pos = find_position(symbol, ticket=ticket)
        if not pos:
            reconcile_missing(trade)
            continue
        trade["missing_runs"] = 0
        trade["last_price"] = float(pos.get("price_current") or trade.get("last_price") or 0)

        horizon_secs = int(trade.get("horizon_bars") or 48) * 30 * 60
        if design == "A" and now - float(trade.get("opened_ts") or now) >= horizon_secs:
            log(f"{ticket}: Design A horizon reached; closing")
            if is_dry():
                log(f"[dry-run] would close {ticket}")
                continue
            try:
                call("POST", "/order/close", json={"ticket": int(ticket)})
                mark_closed(trade, "HORIZON", float(trade.get("last_price") or trade.get("entry") or 0))
            except Exception as exc:
                log(f"{ticket}: horizon close failed: {str(exc)[:140]}")
                failures += 1
            continue

        if now - float(trade.get("last_ratchet_ts") or trade.get("opened_ts") or now) >= HOUR_SECS * 0.9:
            failures += tighten_stop(trade, pos)
    return failures


# -- opening new signals -----------------------------------------------------------
def open_signal(sig: Dict, trades: List[Dict], max_open: int, suffix: str, max_age_min: float, max_drift: float) -> int:
    sid = sig["signal_id"]
    tag = sid[:31]
    symbol = sig["pair"] + suffix
    side = str(sig["side"]).upper()
    design = str(sig.get("design", "A")).upper()

    age = (time.time() - float(sig["signal_ts"])) / 60 if sig.get("signal_ts") else 0
    if age > max_age_min:
        log(f"{sid}: stale ({age:.0f} min); skipped")
        return 0
    if any(t.get("signal_id") == sid for t in trades):
        log(f"{sid}: already in ledger; skipped")
        return 0
    if side not in ("BUY", "SELL"):
        log(f"{sid}: bad side")
        return 1
    if find_position(symbol, tag):
        log(f"{sid}: position with this tag already open; skipped")
        return 0

    mine = [p for p in safe_list("/positions_get") if str(p.get("comment", "")).startswith("TSE-")]
    if any(p.get("symbol") == symbol and str(p.get("comment", "")).startswith(f"TSE-{design}-") for p in mine):
        log(f"{sid}: Design {design} already has a position on {symbol}; skipped")
        return 0
    if len(mine) >= max_open:
        log(f"{sid}: {len(mine)} TSE positions open (max {max_open}); skipped")
        return 0

    try:
        call("POST", "/symbol_select", json={"symbol": symbol, "enable": True})
    except Exception:
        pass
    tick = call("GET", f"/symbol_info_tick/{symbol}")
    bid, ask = float(tick["bid"]), float(tick["ask"])
    price = ask if side == "BUY" else bid

    sl_dist, tp_dist = float(sig["sl_dist"]), float(sig.get("tp_dist") or 0)
    if sl_dist <= 0:
        log(f"{sid}: invalid SL distance")
        return 1
    if abs(price - float(sig["entry"])) > max_drift * sl_dist:
        log(f"{sid}: price moved {abs(price - float(sig['entry'])):.5f} since the signal (limit {max_drift * sl_dist:.5f}); skipped")
        return 0
    gap = min_stop_gap(symbol)
    if sl_dist < gap or (tp_dist and tp_dist < gap):
        log(f"{sid}: stop/target distance below the broker minimum ({gap:.5f}); skipped")
        return 0

    sl = normalize_price(symbol, price - sl_dist if side == "BUY" else price + sl_dist)
    tp = normalize_price(symbol, price + tp_dist if side == "BUY" else price - tp_dist) if tp_dist > 0 else 0.0
    lot = calc_lot(symbol, sl_dist)
    order = {"symbol": symbol, "order_type": side, "volume": lot, "sl": sl, "comment": tag}
    if tp > 0:
        order["tp"] = tp

    if is_dry():
        log(f"[dry-run] {design} {side} {lot} {symbol} @ {price} SL={sl} TP={tp or 'none (horizon)'}")
        return 0

    try:
        call("POST", "/order/open", json=order)
    except Exception as exc:
        log(f"{sid}: order failed: {str(exc)[:160]}")
        send_telegram(f"TSE ORDER FAILED\n{symbol} {side} Design {design}\n{str(exc)[:200]}")
        return 1

    pos = None
    for _ in range(8):
        pos = find_position(symbol, tag)
        if pos:
            break
        time.sleep(2)
    if not pos:
        log(f"{sid}: order sent but position not found; CHECK THE ACCOUNT")
        send_telegram(f"TSE AMBIGUOUS\n{symbol} {side} Design {design}\nCheck MT5 manually")
        return 1

    entry_px = float(pos.get("price_open", price))
    sl_px = float(pos.get("sl") or sl)
    tp_px = float(pos.get("tp") or tp)
    trades.append({
        "signal_id": sid, "ticket": pos.get("ticket"), "symbol": symbol, "pair": sig.get("pair", ""),
        "side": side, "design": design, "entry": entry_px, "sl": sl_px, "tp": tp_px,
        "sl_initial": sl_px, "tp_initial": tp_px, "sl_dist": sl_dist, "tp_dist": tp_dist, "lot": lot,
        "status": "OPEN", "opened_ts": time.time(), "last_ratchet_ts": time.time(), "ratchet_count": 0,
        "horizon_bars": int(sig.get("horizon_bars") or 48), "atr": sig.get("atr"),
        "hourly_atr": sig.get("hourly_atr") or sig.get("atr"),
        "hourly_atr_mult": float(sig.get("hourly_atr_mult") or 1.0),
        "pattern": sig.get("pattern"), "strategy_id": sig.get("strategy_id", ""),
        "strategy_rank": sig.get("strategy_rank", ""), "research_net": sig.get("research_net", ""),
        "comment": tag, "mode": sig.get("mode"),
    })
    try:
        with open(COOLDOWN_FILE) as f:
            cds = json.load(f)
    except (OSError, ValueError):
        cds = {}
    cds[sig["pair"]] = time.time()
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(cds, f, indent=2)

    send_telegram(f"TSE EXECUTED [{design}]\n{side} {lot} {symbol}\nEntry {pos.get('price_open')} SL {pos.get('sl')} "
                  f"{'TP ' + str(tp_px) if tp_px else 'TP none (24h horizon)'}\nPattern {sig.get('pattern')} | Ticket {pos.get('ticket')}")
    log(f"{sid}: filled {side} {lot} {symbol} ticket={pos.get('ticket')}")
    return 0


# -- entry point ---------------------------------------------------------------------
def load_signals() -> List[Dict]:
    raw = env("SIGNALS_JSON")
    if raw:
        return json.loads(raw)
    try:
        with open("signals_out.json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def main() -> int:
    if "--wait" in sys.argv:
        wait_ready(int(env("READY_TIMEOUT", "300")))
        return 0

    try:
        signals = load_signals()
    except ValueError:
        sys.exit("signals file is not valid JSON")
    suffix = env("SYMBOL_SUFFIX")
    max_age_min = float(env("MAX_AGE_MIN", "25"))
    max_open = int(env("MAX_OPEN", "4"))
    max_drift = float(env("MAX_DRIFT_FRAC", "0.25"))
    dry = is_dry()
    log(f"mode: {'DRY RUN (nothing is sent)' if dry else 'LIVE'}")

    trades = load_trades()
    wait_ready(int(env("READY_TIMEOUT", "300")))
    ti = call("GET", "/terminal_info")
    if not ti.get("trade_allowed", False) and not dry:
        sys.exit("terminal reports trade_allowed = false (algo trading off); refusing to trade")

    failures = 0
    try:
        failures += manage(trades)
        for sig in signals:
            try:
                failures += open_signal(sig, trades, max_open, suffix, max_age_min, max_drift)
            except Exception as exc:
                failures += 1
                log(f"{sig.get('signal_id', '?')}: error: {str(exc)[:160]}")
    finally:
        if not dry:
            save_trades(trades)
    if failures:
        sys.exit(f"{failures} problem(s); check the account manually")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
