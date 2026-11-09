"""
MT5 Signal Receiver (Hudsonventura-compatible)
==============================================
Lightweight component that runs inside or alongside the Hudsonventura
MT5 Docker environment. Responsibilities:

  1. Receive signal records from the TSE Signal Output Interface
  2. Validate structure
  3. Detect duplicates
  4. Check completeness
  5. Record receipt + processing status
  6. Support controlled demonstration / non-live validation

Never recalculates strategy logic. Never talks to Liberatti.
"""

from .receiver import MT5SignalReceiver

__all__ = ["MT5SignalReceiver"]
