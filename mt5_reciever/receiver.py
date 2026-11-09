"""
MT5-side Signal Receiver for Hudsonventura environment.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

import click
from signal_interface import json_compat as orjson
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signal_interface.models import SignalRecord
from signal_interface.validator import SignalValidator

console = Console()


class ReceiptStatus:
    RECEIVED = "RECEIVED"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class MT5SignalReceiver:
    def __init__(self, audit_dir: Path):
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.received_ids: Set[str] = set()
        self.receipts: List[Dict] = []
        self._load_existing_receipts()

    def _receipts_path(self) -> Path:
        return self.audit_dir / "receipts.jsonl"

    def _load_existing_receipts(self) -> None:
        path = self._receipts_path()
        if not path.exists():
            return
        with path.open("rb") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = orjson.loads(line)
                    self.received_ids.add(rec["signal_id"])
                    self.receipts.append(rec)
                except Exception:
                    continue

    def _append_receipt(self, receipt: Dict) -> None:
        with self._receipts_path().open("ab") as fh:
            fh.write(orjson.dumps(receipt) + b"\n")
        self.receipts.append(receipt)

    def process_record(self, data: dict) -> Dict:
        now = datetime.now(timezone.utc).isoformat()
        signal_id = data.get("signal_id", "<missing>")

        validator = SignalValidator()
        ok, record = validator.validate_record(data)

        if not ok:
            receipt = {
                "signal_id": signal_id,
                "status": ReceiptStatus.INVALID,
                "received_at": now,
                "errors": validator.errors,
                "original": data,
            }
            self._append_receipt(receipt)
            return receipt

        assert record is not None

        if record.signal_id in self.received_ids:
            receipt = {
                "signal_id": record.signal_id,
                "status": ReceiptStatus.DUPLICATE,
                "received_at": now,
                "strategy_id": record.strategy_id,
                "instrument": record.instrument,
                "design": record.design.value,
            }
            self._append_receipt(receipt)
            return receipt

        self.received_ids.add(record.signal_id)
        receipt = {
            "signal_id": record.signal_id,
            "status": ReceiptStatus.RECEIVED,
            "received_at": now,
            "strategy_id": record.strategy_id,
            "instrument": record.instrument,
            "direction": record.direction.value,
            "design": record.design.value,
            "signal_timestamp": record.signal_timestamp.isoformat(),
            "entry_reference": record.entry_reference,
            "stop_loss_reference": record.stop_loss_reference,
            "take_profit_reference": record.take_profit_reference,
            "research_net": record.research_net,
            "horizon_bars": record.horizon_bars,
            "executed": record.executed,
            "research_parameters": record.research_parameters,
            "tse_version": record.tse_version,
        }
        self._append_receipt(receipt)
        return receipt

    def process_jsonl(self, path: Path) -> List[Dict]:
        results: List[Dict] = []
        with path.open("rb") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = orjson.loads(raw)
                except orjson.JSONDecodeError as exc:
                    results.append({
                        "signal_id": f"<line-{lineno}>",
                        "status": ReceiptStatus.FAILED,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "errors": [f"JSON decode error: {exc}"],
                    })
                    continue
                results.append(self.process_record(data))
        return results

    def summary(self) -> Dict:
        counts: Dict[str, int] = {}
        for r in self.receipts:
            status = r.get("status", "UNKNOWN")
            counts[status] = counts.get(status, 0) + 1
        return {
            "total_receipts": len(self.receipts),
            "unique_signal_ids": len(self.received_ids),
            "by_status": counts,
            "audit_dir": str(self.audit_dir),
        }


@click.command()
@click.option("--input", "input_path", type=click.Path(exists=True), required=True)
@click.option("--audit-dir", type=click.Path(), default="./audit")
def main(input_path: str, audit_dir: str) -> None:
    """Receive and process a batch of TSE signals (demonstration / validation mode)."""
    receiver = MT5SignalReceiver(audit_dir=Path(audit_dir))
    results = receiver.process_jsonl(Path(input_path))

    table = Table(title="MT5 Receiver – Processing Results")
    table.add_column("Signal ID", style="cyan")
    table.add_column("Status")
    table.add_column("Instrument")
    table.add_column("Dir")
    table.add_column("Design")

    for r in results:
        status = r.get("status", "?")
        style = {
            ReceiptStatus.RECEIVED: "green",
            ReceiptStatus.DUPLICATE: "yellow",
            ReceiptStatus.INVALID: "red",
            ReceiptStatus.FAILED: "red",
        }.get(status, "")
        table.add_row(
            str(r.get("signal_id", ""))[:32],
            f"[{style}]{status}[/]",
            r.get("instrument", ""),
            r.get("direction", ""),
            r.get("design", ""),
        )
    console.print(table)

    summary = receiver.summary()
    console.print("\n[bold]Summary[/]")
    console.print(json.dumps(summary, indent=2))

    summary_path = Path(audit_dir) / "receiver_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    console.print(f"\n[green]Audit artefacts written to {audit_dir}[/]")


if __name__ == "__main__":
    main()
