#!/usr/bin/env python3
"""
Cheap pre-check (standard library only) so the expensive steps - installing packages and starting
the MT5 terminal - run ONLY when something can happen:
  * a London/NY session is open (new signals possible), or
  * the ledger has an open trade that needs managing (hourly stop ratchet / 24h horizon close), or
  * the run was forced by hand (KB_FORCE_SCAN=true).
Writes `active=true|false` to $GITHUB_OUTPUT.
"""
import json
import os
import sys
import time

from utils.time_utils import is_active_session, now_utc
from config import STATE_DIR


def open_trades() -> int:
    try:
        with open(os.path.join(STATE_DIR, "trades.json")) as f:
            trades = json.load(f)
    except (OSError, ValueError):
        return 0
    return sum(1 for t in trades if t.get("status") == "OPEN")


def main() -> int:
    force = os.environ.get("KB_FORCE_SCAN", "false").lower() == "true"
    weekday = now_utc().weekday() < 5
    session = weekday and is_active_session()
    n_open = open_trades()
    active = force or session or (weekday and n_open > 0)
    print(f"gate: weekday={weekday} session={session} open_trades={n_open} force={force} -> active={active}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"active={'true' if active else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
