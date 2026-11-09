#!/usr/bin/env python3
"""
MT5 demonstration / validation executor
=======================================
Consumes canonical SignalRecords produced by the Signal Output Interface.

This component is intentionally limited to:
  - loading and validating canonical signals
  - recording what *would* be sent to the Hudsonventura MT5 environment
  - supporting the permanent signal-equivalence audit

It does NOT:
  - talk to Liberatti
  - place live orders
  - implement a second strategy engine

Live order placement is performed by the MQL5 EA (TSE_SignalExecutor)
inside Hudsonventura after signals pass the equivalence audit and are
written to MQL5/Files/tse_signals.csv.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from signal_interface.adapter import records_from_scan_once
from signal_interface.exporter import SignalExporter
from signal_interface.models import SignalRecord
from signal_interface.validator import SignalValidator
from mt5_receiver.receiver import MT5SignalReceiver, ReceiptStatus


STATE_DIR = os.environ.get("KB_STATE_DIR", "state")
SIGNALS_LEGACY = "signals_out.json"          # still written by scan_once
SIGNALS_CANONICAL = os.path.join(STATE_DIR, "signals_canonical.jsonl")
AUDIT_DIR = os.environ.get("KB_AUDIT_DIR", "audit")


def log(msg: str) -> None:
    print(msg, flush=True)


def load_legacy_signals(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def ensure_canonical(legacy_path: str, canonical_path: str) -> Path:
    """
    If scan_once already wrote the canonical JSONL, use it.
    Otherwise convert the legacy signals_out.json through the adapter.
    """
    cpath = Path(canonical_path)
    if cpath.exists() and cpath.stat().st_size > 0:
        log(f"Using existing canonical signals: {cpath}")
        return cpath

    legacy = load_legacy_signals(legacy_path)
    if not legacy:
        log("No signals to process")
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text("")
        return cpath

    records = records_from_scan_once(legacy)
    n = SignalExporter().export_jsonl(records, cpath)
    log(f"Converted {n} legacy records → canonical JSONL ({cpath})")
    return cpath


def main() -> int:
    dry = os.environ.get("DRY_RUN", "true").lower() != "false"
    log(f"MT5 demonstration executor  |  DRY_RUN={dry}")

    # 1. Ensure we have canonical signals
    canonical = ensure_canonical(SIGNALS_LEGACY, SIGNALS_CANONICAL)

    # 2. Schema validation
    validator = SignalValidator()
    ok, records = validator.validate_jsonl(canonical)
    if not ok:
        log("Schema validation FAILED:")
        log(validator.report())
        return 1
    log(f"Schema OK – {len(records)} signal(s)")

    if not records:
        log("Nothing to hand to the receiver")
        return 0

    # 3. Hand to MT5 Signal Receiver (audit trail)
    receiver = MT5SignalReceiver(audit_dir=Path(AUDIT_DIR))
    results = receiver.process_jsonl(canonical)

    received = sum(1 for r in results if r["status"] == ReceiptStatus.RECEIVED)
    duplicates = sum(1 for r in results if r["status"] == ReceiptStatus.DUPLICATE)
    invalids = sum(1 for r in results if r["status"] in (ReceiptStatus.INVALID, ReceiptStatus.FAILED))

    log(f"Receiver: received={received}  duplicates={duplicates}  invalid/failed={invalids}")

    summary = receiver.summary()
    summary_path = Path(AUDIT_DIR) / "receiver_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    log(f"Audit summary → {summary_path}")

    if invalids:
        log("One or more signals failed validation – see audit/")
        return 1

    # 4. Demonstration ledger
    ledger_path = Path(STATE_DIR) / "demo_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    from signal_interface import json_compat as orjson
    with ledger_path.open("ab") as fh:
        for rec in records:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "signal_id": rec.signal_id,
                "instrument": rec.instrument,
                "direction": rec.direction.value,
                "design": rec.design.value,
                "entry": rec.entry_reference,
                "sl": rec.stop_loss_reference,
                "tp": rec.take_profit_reference,
                "dry_run": dry,
            }
            fh.write(orjson.dumps(entry) + b"\n")
    log(f"Demo ledger updated → {ledger_path}")

    # 5. EA drop-file for Hudsonventura (MQL5/Files/tse_signals.csv)
    from signal_interface.ea_drop import write_ea_csv
    ea_targets = [
        Path(os.environ.get("KB_EA_DROP", "mql5/Files/tse_signals.csv")),
        Path(".data/MQL5/Files/tse_signals.csv"),  # Hudsonventura default mount
    ]
    for target in ea_targets:
        n = write_ea_csv(records, target, append=False)
        log(f"EA drop-file ({n} rows) → {target}")

    if dry:
        log("DRY_RUN=true: EA should keep InpDryRun=true until you are ready.")
    else:
        log("DRY_RUN=false: ensure the EA InpDryRun=false inside Hudsonventura to place live orders.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"Fatal: {e}")
        sys.exit(1)
