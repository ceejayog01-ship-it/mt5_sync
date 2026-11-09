"""
Write an EA-friendly CSV drop-file for the Hudsonventura MQL5 EA.

Canonical JSONL remains the source of truth for the audit.
This CSV is a simplified projection that pure MQL5 can parse reliably.

Format (header + one row per signal):
  signal_id,instrument,direction,design,entry,sl,tp,horizon_bars

Design A → tp field is empty (EA treats as no TP).
Design B → tp is the research take-profit price.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .models import Design, SignalRecord


CSV_HEADER = "signal_id,instrument,direction,design,entry,sl,tp,horizon_bars"


def signal_to_csv_row(sig: SignalRecord) -> str:
    tp = ""
    if sig.design == Design.B and sig.take_profit_reference is not None:
        tp = f"{sig.take_profit_reference:.5f}"
    horizon = sig.horizon_bars if sig.horizon_bars is not None else 48
    return (
        f"{sig.signal_id},"
        f"{sig.instrument},"
        f"{sig.direction.value},"
        f"{sig.design.value},"
        f"{sig.entry_reference:.5f},"
        f"{sig.stop_loss_reference:.5f},"
        f"{tp},"
        f"{horizon}"
    )


def write_ea_csv(signals: Iterable[SignalRecord], dest: Path, append: bool = False) -> int:
    """
    Write / append EA CSV. Returns number of rows written (excluding header).
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    rows: List[str] = [signal_to_csv_row(s) for s in signals]
    mode = "a" if append and dest.exists() else "w"
    with dest.open(mode, encoding="utf-8") as fh:
        if mode == "w":
            fh.write(CSV_HEADER + "\n")
        for row in rows:
            fh.write(row + "\n")
    return len(rows)
