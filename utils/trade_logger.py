"""
Persistent logger for every strategy signal and its outcome (SL / TP / HORIZON / …).

Files (under STATE_DIR, default state/):
  signals_log.csv   — one row per emitted signal
  outcomes_log.csv  — one row per closed trade (linked by signal_id)

Both are append-only and safe to load into pandas for analysis.
"""
from __future__ import annotations

import csv
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_LOCK = threading.Lock()

SIGNAL_FIELDS = [
    "logged_at",
    "signal_id",
    "pair",
    "side",
    "design",
    "mode",
    "strategy_id",
    "strategy_rank",
    "pattern",
    "plain_a",
    "plain_b",
    "entry",
    "sl",
    "tp",
    "sl_dist",
    "tp_dist",
    "atr",
    "horizon_bars",
    "research_net",
    "bar_time",
    "signal_ts",
    "executed",
    "calibrated",
]

OUTCOME_FIELDS = [
    "logged_at",
    "signal_id",
    "ticket",
    "pair",
    "symbol",
    "side",
    "design",
    "strategy_id",
    "strategy_rank",
    "pattern",
    "entry",
    "sl_initial",
    "tp_initial",
    "exit_price",
    "exit_reason",          # SL | TP | HORIZON | BE | TIME | UNKNOWN | DRY
    "r_price",              # price PnL (exit - entry) signed for side
    "r_r",                  # PnL in R (vs initial sl_dist)
    "bars_held",
    "hours_held",
    "ratchet_count",
    "opened_ts",
    "closed_ts",
    "research_net",
    "horizon_bars",
]


def _state_dir() -> str:
    return os.environ.get("KB_STATE_DIR", "state")


def _path(name: str) -> str:
    d = _state_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_csv(path: str, fieldnames: list, row: Dict[str, Any]) -> None:
    with _LOCK:
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in fieldnames})


def log_signal(sig: Dict[str, Any]) -> None:
    """Record an emitted strategy signal (call from scan_once)."""
    row = {
        "logged_at": _now(),
        "signal_id": sig.get("signal_id", ""),
        "pair": sig.get("pair", ""),
        "side": sig.get("side", ""),
        "design": sig.get("design", ""),
        "mode": sig.get("mode", ""),
        "strategy_id": sig.get("strategy_id", ""),
        "strategy_rank": sig.get("strategy_rank", ""),
        "pattern": sig.get("pattern", ""),
        "plain_a": sig.get("plain_a", ""),
        "plain_b": sig.get("plain_b", ""),
        "entry": sig.get("entry", ""),
        "sl": sig.get("sl", ""),
        "tp": sig.get("tp", ""),
        "sl_dist": sig.get("sl_dist", ""),
        "tp_dist": sig.get("tp_dist", ""),
        "atr": sig.get("atr", ""),
        "horizon_bars": sig.get("horizon_bars", ""),
        "research_net": sig.get("research_net", ""),
        "bar_time": sig.get("bar_time", ""),
        "signal_ts": sig.get("signal_ts", ""),
        "executed": sig.get("executed", ""),
        "calibrated": sig.get("calibrated", ""),
    }
    _append_csv(_path("signals_log.csv"), SIGNAL_FIELDS, row)


def log_outcome(
    trade: Dict[str, Any],
    exit_reason: str,
    exit_price: Optional[float] = None,
    bars_held: Optional[int] = None,
) -> None:
    """
    Record a closed trade outcome (call from mt5_execute when status → CLOSED).

    exit_reason: SL | TP | HORIZON | TIME | UNKNOWN | DRY
    """
    side = str(trade.get("side", "")).upper()
    entry = float(trade.get("entry") or 0)
    sl_dist = float(trade.get("sl_dist") or 0)
    if sl_dist <= 0:
        # recover from initial sl if needed
        sl0 = float(trade.get("sl_initial") or trade.get("sl") or 0)
        if entry and sl0:
            sl_dist = abs(entry - sl0)

    ep = float(exit_price) if exit_price is not None else float(trade.get("exit_price") or 0)
    if entry and ep:
        r_price = (ep - entry) if side == "BUY" else (entry - ep)
    else:
        r_price = 0.0
    r_r = (r_price / sl_dist) if sl_dist > 0 else 0.0

    opened = float(trade.get("opened_ts") or 0)
    closed = float(trade.get("closed_ts") or 0)
    hours = ((closed - opened) / 3600.0) if opened and closed else ""

    # pair from symbol (strip suffix) or explicit
    symbol = str(trade.get("symbol") or "")
    pair = str(trade.get("pair") or symbol[:6] if symbol else "")

    row = {
        "logged_at": _now(),
        "signal_id": trade.get("signal_id", ""),
        "ticket": trade.get("ticket", ""),
        "pair": pair,
        "symbol": symbol,
        "side": side,
        "design": trade.get("design", ""),
        "strategy_id": trade.get("strategy_id", ""),
        "strategy_rank": trade.get("strategy_rank", ""),
        "pattern": trade.get("pattern", ""),
        "entry": entry,
        "sl_initial": trade.get("sl_initial", trade.get("sl", "")),
        "tp_initial": trade.get("tp_initial", trade.get("tp", "")),
        "exit_price": ep or "",
        "exit_reason": exit_reason,
        "r_price": round(r_price, 6),
        "r_r": round(r_r, 4),
        "bars_held": bars_held if bars_held is not None else trade.get("bars_held", ""),
        "hours_held": round(hours, 2) if hours != "" else "",
        "ratchet_count": trade.get("ratchet_count", 0),
        "opened_ts": opened or "",
        "closed_ts": closed or "",
        "research_net": trade.get("research_net", ""),
        "horizon_bars": trade.get("horizon_bars", ""),
    }
    _append_csv(_path("outcomes_log.csv"), OUTCOME_FIELDS, row)


def infer_exit_reason(trade: Dict[str, Any], exit_price: float) -> str:
    """Best-effort classification when broker only tells us the close price."""
    if trade.get("close_reason"):
        return str(trade["close_reason"])
    side = str(trade.get("side", "")).upper()
    entry = float(trade.get("entry") or 0)
    sl = float(trade.get("sl_initial") or trade.get("sl") or 0)
    tp = float(trade.get("tp_initial") or trade.get("tp") or 0)
    if not entry or not exit_price:
        return "UNKNOWN"
    # tolerance: 15% of SL distance
    sl_dist = abs(entry - sl) if sl else 0
    tol = max(sl_dist * 0.15, 1e-5)
    if sl and abs(exit_price - sl) <= tol:
        return "SL"
    if tp and tp > 0 and abs(exit_price - tp) <= tol:
        return "TP"
    # Design A horizon already tagged by executor
    if str(trade.get("design", "")).upper() == "A":
        return "HORIZON_OR_MANUAL"
    return "UNKNOWN"
