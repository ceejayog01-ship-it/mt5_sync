"""
Canonical signal schema.
Maps 1:1 onto the fields already produced by scan_once.build_records().
Every critical field required by the architecture is present and typed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Design(str, Enum):
    """Matches the two designs already defined in the TSE executor."""
    A = "A"  # Research: SL only, time-exit after horizon_bars
    B = "B"  # Hybrid: SL + TP from research net


class SignalRecord(BaseModel):
    """
    Single immutable signal record produced by the original TSE engine.
    This is the contract that both the exporter and the MT5 receiver must honour.
    """

    # --- Required identity & routing ---
    signal_id: str = Field(
        ...,
        description="Globally unique identifier. Prefer the ID already assigned by TSE; never regenerate downstream.",
        min_length=8,
        max_length=96,
    )
    strategy_id: str = Field(..., min_length=1, max_length=64)
    instrument: str = Field(..., min_length=3, max_length=32, description="e.g. EURUSD")
    direction: Direction
    design: Design = Field(..., description="A = research time-exit, B = hybrid with TP")

    # --- Timing ---
    signal_timestamp: datetime = Field(
        ...,
        description="UTC timestamp of the M30 bar / moment TSE generated the signal.",
    )
    bar_time: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of the completed M30 bar that produced the signal.",
    )

    # --- Price references (exact values from TSE, never recomputed) ---
    entry_reference: float
    stop_loss_reference: float
    take_profit_reference: Optional[float] = Field(
        default=None,
        description="Present for Design B; None for Design A (time-exit).",
    )

    # --- Research context carried from the original engine ---
    research_parameters: Dict[str, Any] = Field(default_factory=dict)
    research_net: Optional[float] = Field(
        default=None,
        description="OOS research net return used for Design B TP calculation.",
    )
    horizon_bars: Optional[int] = Field(
        default=None,
        description="Design A exit horizon in M30 bars (default 48 ≈ 24h).",
    )
    strategy_rank: Optional[int] = None
    plain_a: Optional[str] = None
    plain_b: Optional[str] = None
    pattern: Optional[str] = None
    hourly_atr: Optional[float] = None
    calibrated: bool = False
    executed: bool = Field(
        default=False,
        description="True when the pair is in KB_EXEC_PAIRS (handed to executor).",
    )

    # --- Metadata for audit trail ---
    tse_version: Optional[str] = None
    batch_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {
        "frozen": True,
        "extra": "forbid",
    }

    @field_validator("signal_timestamp", "created_at", "bar_time", mode="before")
    @classmethod
    def ensure_timezone_aware(cls, v: Any) -> Optional[datetime]:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
        elif isinstance(v, datetime):
            dt = v
        elif isinstance(v, (int, float)):
            dt = datetime.fromtimestamp(v, tz=timezone.utc)
        else:
            raise TypeError("timestamp must be datetime, ISO string, or unix seconds")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


class SignalBatch(BaseModel):
    """Container for a set of signals emitted together."""

    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(default="TSE_ORIGINAL_ENGINE")
    signals: List[SignalRecord] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def __len__(self) -> int:
        return len(self.signals)

    def signal_ids(self) -> List[str]:
        return [s.signal_id for s in self.signals]
