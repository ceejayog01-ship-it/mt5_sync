"""
Adapter: convert the dicts already produced by scan_once.build_records()
into canonical SignalRecord objects without changing any strategy logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from .models import Design, Direction, SignalRecord


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def record_from_scan_dict(raw: Dict[str, Any], batch_id: str | None = None) -> SignalRecord:
    """
    Map one dict from scan_once.build_records() → SignalRecord.

    Expected keys from the template (see scan_once.py):
      pair, side, design, entry, sl, tp, pattern, strategy_id, strategy_rank,
      plain_a, plain_b, horizon_bars, research_net, bar_time, signal_ts,
      hourly_atr, executed, calibrated, ...
    """
    pair = str(raw.get("pair") or raw.get("instrument") or "").upper()
    side = str(raw.get("side") or raw.get("direction") or "BUY").upper()
    design_raw = str(raw.get("design") or "A").upper()
    design = Design.B if design_raw == "B" else Design.A

    # Prefer an ID already present; otherwise synthesise a stable-looking one
    signal_id = raw.get("signal_id") or raw.get("id")
    if not signal_id:
        ts = raw.get("signal_ts") or raw.get("bar_time") or ""
        strategy = raw.get("strategy_id") or "UNK"
        signal_id = f"tse-{pair}-{design.value}-{strategy}-{ts}-{uuid4().hex[:6]}"

    bar_time = raw.get("bar_time")
    signal_ts = raw.get("signal_ts") or raw.get("signal_timestamp") or bar_time

    tp = raw.get("tp")
    if tp is None:
        tp = raw.get("take_profit_reference")

    # Design A is time-exit only: never treat 0.0 as a real TP
    if design == Design.A:
        tp_ref = None
    elif tp is None:
        tp_ref = None
    else:
        try:
            tp_val = float(tp)
            tp_ref = None if tp_val == 0.0 else tp_val
        except (TypeError, ValueError):
            tp_ref = None

    return SignalRecord(
        signal_id=str(signal_id),
        strategy_id=str(raw.get("strategy_id") or "UNKNOWN"),
        instrument=pair,
        direction=Direction.BUY if side == "BUY" else Direction.SELL,
        design=design,
        signal_timestamp=_parse_ts(signal_ts),
        bar_time=_parse_ts(bar_time) if bar_time else None,
        entry_reference=float(raw["entry"] if "entry" in raw else raw["entry_reference"]),
        stop_loss_reference=float(raw["sl"] if "sl" in raw else raw["stop_loss_reference"]),
        take_profit_reference=tp_ref,
        research_parameters={
            k: raw[k]
            for k in ("pattern", "plain_a", "plain_b", "hourly_atr_mult")
            if k in raw
        },
        research_net=float(raw["research_net"]) if raw.get("research_net") is not None else None,
        horizon_bars=int(raw["horizon_bars"]) if raw.get("horizon_bars") is not None else None,
        strategy_rank=int(raw["strategy_rank"]) if raw.get("strategy_rank") is not None else None,
        plain_a=raw.get("plain_a"),
        plain_b=raw.get("plain_b"),
        pattern=raw.get("pattern"),
        hourly_atr=float(raw["hourly_atr"]) if raw.get("hourly_atr") is not None else None,
        calibrated=bool(raw.get("calibrated", False)),
        executed=bool(raw.get("executed", False)),
        tse_version=raw.get("tse_version"),
        batch_id=batch_id,
    )


def records_from_scan_once(raw_list: List[Dict[str, Any]], batch_id: str | None = None) -> List[SignalRecord]:
    """Convert a full list of scan_once executor records into SignalRecords."""
    bid = batch_id or str(uuid4())
    return [record_from_scan_dict(r, batch_id=bid) for r in raw_list]
