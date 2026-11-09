"""
Signal Output Interface – Exporter
Serialises original TSE signals into the canonical JSONL / JSON format.
Never generates trading decisions.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, TextIO

import click
from . import json_compat as orjson
from rich.console import Console
from rich.table import Table

from .models import Design, Direction, SignalBatch, SignalRecord

console = Console()


class SignalExporter:
    def __init__(self, pretty: bool = False):
        self.pretty = pretty

    def export_jsonl(self, signals: Iterable[SignalRecord], dest: TextIO | Path) -> int:
        count = 0
        if isinstance(dest, Path):
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                for sig in signals:
                    fh.write(orjson.dumps(sig.model_dump(mode="json")) + b"\n")
                    count += 1
        else:
            for sig in signals:
                dest.write(orjson.dumps(sig.model_dump(mode="json")).decode() + "\n")
                count += 1
        return count

    def export_batch(self, batch: SignalBatch, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = batch.model_dump(mode="json")
        if self.pretty:
            dest.write_text(json.dumps(payload, indent=2, default=str))
        else:
            dest.write_bytes(orjson.dumps_pretty(payload) if hasattr(orjson, 'dumps_pretty') else orjson.dumps(payload))


def _demo_signals(count: int = 4) -> List[SignalRecord]:
    """Small deterministic batch for CI / local demo (not real strategy output)."""
    from datetime import timedelta
    from uuid import uuid4

    base = datetime(2026, 11, 8, 9, 0, 0, tzinfo=timezone.utc)
    strategies = [
        "STRAT_001_BREAKOUT",
        "STRAT_017_MEAN_REVERSION",
        "STRAT_042_TREND_PULLBACK",
        "STRAT_009_SESSION_OPEN",
    ]
    instruments = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY"]
    directions = [Direction.BUY, Direction.SELL, Direction.BUY, Direction.SELL]
    designs = [Design.A, Design.B, Design.A, Design.B]

    out: List[SignalRecord] = []
    for i in range(count):
        ts = base + timedelta(minutes=i * 30)
        entry = 1.0800 + i * 0.0012
        direction = directions[i % len(directions)]
        design = designs[i % len(designs)]
        sl = entry - 0.0030 if direction == Direction.BUY else entry + 0.0030
        tp = (entry + 0.0050 if direction == Direction.BUY else entry - 0.0050) if design == Design.B else None
        out.append(
            SignalRecord(
                signal_id=f"tse-demo-{ts.strftime('%Y%m%d')}-{i+1:04d}-{uuid4().hex[:6]}",
                strategy_id=strategies[i % len(strategies)],
                instrument=instruments[i % len(instruments)],
                direction=direction,
                design=design,
                signal_timestamp=ts,
                bar_time=ts,
                entry_reference=round(entry, 5),
                stop_loss_reference=round(sl, 5),
                take_profit_reference=round(tp, 5) if tp is not None else None,
                research_net=0.0013 if design == Design.B else None,
                horizon_bars=48 if design == Design.A else None,
                strategy_rank=i + 1,
                calibrated=True,
                executed=(instruments[i % len(instruments)] == "EURUSD"),
                tse_version="demo-1.0.0",
            )
        )
    return out


@click.command()
@click.option("--demo", is_flag=True, help="Generate a demonstration batch")
@click.option("--count", default=4, show_default=True)
@click.option("--out", type=click.Path(), default="example_signals/demo_batch.jsonl")
@click.option("--pretty", is_flag=True)
def main(demo: bool, count: int, out: str, pretty: bool) -> None:
    if not demo:
        console.print(
            "[yellow]No live TSE engine attached via this CLI.[/]\n"
            "Use --demo, or call SignalExporter from scan_once after it builds records."
        )
        sys.exit(1)

    signals = _demo_signals(count)
    batch = SignalBatch(
        batch_id=f"demo-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        signals=signals,
        source="TSE_DEMO_GENERATOR",
    )
    out_path = Path(out)
    exporter = SignalExporter(pretty=pretty)
    n = exporter.export_jsonl(signals, out_path)
    console.print(f"[green]Wrote {n} signals → {out_path}[/]")
    summary_path = out_path.with_suffix(".summary.json")
    exporter.export_batch(batch, summary_path)

    table = Table(title="Demonstration Signal Batch")
    table.add_column("Signal ID", style="cyan")
    table.add_column("Strategy")
    table.add_column("Instr")
    table.add_column("Dir")
    table.add_column("Design")
    table.add_column("Entry")
    for s in signals:
        table.add_row(
            s.signal_id[:28] + "…",
            s.strategy_id,
            s.instrument,
            s.direction.value,
            s.design.value,
            f"{s.entry_reference:.5f}",
        )
    console.print(table)


if __name__ == "__main__":
    main()
