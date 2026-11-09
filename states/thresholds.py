"""Per-pair market-state thresholds (falls back to the frozen EURUSD values)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Tuple

from config import VOL48_P33, VOL48_P67, TREND96_P33, TREND96_P67

_FILE = Path(__file__).resolve().parents[1] / "data" / "pair_thresholds.json"
_EURUSD = {"vol48_p33": VOL48_P33, "vol48_p67": VOL48_P67,
           "trend96_p33": TREND96_P33, "trend96_p67": TREND96_P67}


def get_thresholds(pair: str | None) -> Tuple[Dict[str, float], bool]:
    """Return (thresholds, calibrated). calibrated is False when the pair has no entry."""
    if not pair:
        return dict(_EURUSD), True
    try:
        with open(_FILE) as f:
            table = json.load(f)
    except (OSError, ValueError):
        table = {}
    row = table.get(pair.upper())
    if row and all(k in row for k in _EURUSD):
        return {k: float(row[k]) for k in _EURUSD}, True
    return dict(_EURUSD), False
