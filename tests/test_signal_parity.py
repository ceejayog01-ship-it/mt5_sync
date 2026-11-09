"""
Permanent Signal-Equivalence Audit
==================================
Acceptance gate:
  - every critical field matches exactly
  - no missing / duplicated / altered signals
  - every received signal traces back to its original TSE record
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signal_interface.adapter import records_from_scan_once
from signal_interface.exporter import SignalExporter, _demo_signals
from signal_interface.models import SignalRecord
from signal_interface.validator import SignalValidator
from mt5_receiver.receiver import MT5SignalReceiver, ReceiptStatus


@pytest.fixture
def demo_batch(tmp_path: Path) -> Path:
    signals = _demo_signals(count=6)
    out = tmp_path / "source_batch.jsonl"
    SignalExporter().export_jsonl(signals, out)
    return out


@pytest.fixture
def receiver(tmp_path: Path) -> MT5SignalReceiver:
    return MT5SignalReceiver(audit_dir=tmp_path / "audit")


def test_schema_validation_passes_for_demo(demo_batch: Path):
    validator = SignalValidator()
    ok, records = validator.validate_jsonl(demo_batch)
    assert ok, validator.report()
    assert len(records) == 6


def test_no_duplicates_in_source(demo_batch: Path):
    validator = SignalValidator()
    ok, records = validator.validate_jsonl(demo_batch)
    assert ok
    ids = [r.signal_id for r in records]
    assert len(ids) == len(set(ids))


def test_full_round_trip_parity(demo_batch: Path, receiver: MT5SignalReceiver):
    validator = SignalValidator()
    ok, source_records = validator.validate_jsonl(demo_batch)
    assert ok
    source_by_id = {r.signal_id: r for r in source_records}

    results = receiver.process_jsonl(demo_batch)
    received = [r for r in results if r["status"] == ReceiptStatus.RECEIVED]
    invalids = [r for r in results if r["status"] in (ReceiptStatus.INVALID, ReceiptStatus.FAILED)]

    assert len(invalids) == 0
    assert len(received) == len(source_records)

    for receipt in received:
        sid = receipt["signal_id"]
        assert sid in source_by_id
        src = source_by_id[sid]
        assert receipt["strategy_id"] == src.strategy_id
        assert receipt["instrument"] == src.instrument
        assert receipt["direction"] == src.direction.value
        assert receipt["design"] == src.design.value
        assert receipt["signal_timestamp"] == src.signal_timestamp.isoformat()
        assert receipt["entry_reference"] == src.entry_reference
        assert receipt["stop_loss_reference"] == src.stop_loss_reference
        assert receipt["take_profit_reference"] == src.take_profit_reference

    assert {r["signal_id"] for r in received} == set(source_by_id.keys())


def test_duplicate_detection(demo_batch: Path, receiver: MT5SignalReceiver):
    receiver.process_jsonl(demo_batch)
    results = receiver.process_jsonl(demo_batch)
    assert all(r["status"] == ReceiptStatus.DUPLICATE for r in results)


def test_adapter_from_scan_once_shape():
    """Adapter correctly maps the dict shape produced by scan_once.build_records()."""
    raw = [{
        "signal_id": "TSE-A-EURUSD-202611081200-R01",
        "pair": "EURUSD",
        "side": "BUY",
        "entry": 1.08750,
        "sl": 1.08420,
        "tp": 0.0,
        "design": "A",
        "strategy_id": "STRAT_042_TREND_PULLBACK",
        "strategy_rank": 1,
        "horizon_bars": 48,
        "research_net": 0.0013,
        "bar_time": "2026-11-08T12:00:00+00:00",
        "signal_ts": 1731067200,
        "executed": True,
        "calibrated": True,
        "plain_a": "trend pullback",
        "plain_b": "",
        "pattern": "R01",
        "hourly_atr": 0.0015,
    }]
    records = records_from_scan_once(raw)
    assert len(records) == 1
    r = records[0]
    assert r.instrument == "EURUSD"
    assert r.direction.value == "BUY"
    assert r.design.value == "A"
    assert r.entry_reference == 1.08750
    assert r.stop_loss_reference == 1.08420
    assert r.take_profit_reference is None  # Design A must be null, never 0.0
    assert r.horizon_bars == 48
    assert r.executed is True


def test_immutable_signal_record():
    signals = _demo_signals(1)
    with pytest.raises(Exception):
        signals[0].entry_reference = 999.0  # type: ignore
