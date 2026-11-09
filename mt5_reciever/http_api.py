"""Authenticated HTTPS ingress for GitHub Actions -> Hudsonventura MT5 file inbox."""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from signal_interface.ea_drop import CSV_HEADER, signal_to_csv_row
from signal_interface.models import SignalRecord
from signal_interface.validator import SignalValidator

DATA_DIR = Path(os.getenv("TSE_RECEIVER_DATA", "/data"))
MT5_FILES_DIR = Path(os.getenv("TSE_MT5_FILES", "/mt5-files"))
DB_PATH = DATA_DIR / "inbox.sqlite3"
TOKEN = os.getenv("TSE_DELIVERY_TOKEN", "")
_lock = threading.RLock()
app = FastAPI(title="TSE Hudsonventura Signal Ingress", version="1.0.0")


class SignalBatchIn(BaseModel):
    batch_id: str | None = None
    signals: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not TOKEN:
        raise HTTPException(status_code=503, detail="TSE_DELIVERY_TOKEN is not configured")
    supplied = authorization or ""
    expected = f"Bearer {TOKEN}"
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS inbox (
        signal_id TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        received_at TEXT NOT NULL
    )""")
    return conn


def _publish_ea_snapshot(conn: sqlite3.Connection) -> int:
    """Atomically replace EA CSV from the durable SQLite inbox."""
    MT5_FILES_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("SELECT payload FROM inbox ORDER BY received_at, signal_id").fetchall()
    lines = [CSV_HEADER]
    for (payload,) in rows:
        record = SignalRecord.model_validate_json(payload)
        lines.append(signal_to_csv_row(record))
    content = "\n".join(lines) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".tse_signals_", suffix=".tmp", dir=MT5_FILES_DIR)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, MT5_FILES_DIR / "tse_signals.csv")
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return len(rows)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "tse-hudsonventura-ingress"}


@app.post("/v1/signals", dependencies=[Depends(require_token)])
def receive_signals(batch: SignalBatchIn) -> dict[str, Any]:
    accepted: list[str] = []
    duplicates: list[str] = []
    invalid: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for index, raw in enumerate(batch.signals):
                validator = SignalValidator()
                ok, record = validator.validate_record(raw)
                if not ok or record is None:
                    invalid.append({"index": index, "signal_id": raw.get("signal_id"), "errors": validator.errors})
                    continue
                payload = record.model_dump_json()
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO inbox(signal_id,payload,received_at) VALUES(?,?,?)",
                    (record.signal_id, payload, now),
                )
                (accepted if cursor.rowcount == 1 else duplicates).append(record.signal_id)
            conn.commit()
            total = _publish_ea_snapshot(conn)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return {
        "status": "accepted" if not invalid else "accepted_with_errors",
        "batch_id": batch.batch_id,
        "accepted": accepted,
        "duplicates": duplicates,
        "invalid": invalid,
        "durable_inbox_count": total,
        "ea_file": "MQL5/Files/tse_signals.csv",
    }
