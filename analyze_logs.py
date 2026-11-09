#!/usr/bin/env python3
"""Summarise state/signals_log.csv and state/outcomes_log.csv for analysis."""
from __future__ import annotations
import os
import sys
import pandas as pd

STATE = os.environ.get("KB_STATE_DIR", "state")
SIG = os.path.join(STATE, "signals_log.csv")
OUT = os.path.join(STATE, "outcomes_log.csv")


def main() -> int:
    if not os.path.exists(SIG) and not os.path.exists(OUT):
        print(f"No logs under {STATE}/ (signals_log.csv / outcomes_log.csv)")
        return 1

    if os.path.exists(SIG):
        s = pd.read_csv(SIG)
        print(f"=== Signals ({len(s)}) ===")
        if len(s):
            print(s.groupby(["design", "side", "pattern"], dropna=False).size().rename("n").reset_index().to_string(index=False))
            print("\nBy strategy_rank:")
            print(s.groupby("strategy_rank").size().rename("n").head(20))
    else:
        print("No signals_log.csv yet")

    if os.path.exists(OUT):
        o = pd.read_csv(OUT)
        print(f"\n=== Outcomes ({len(o)}) ===")
        if len(o):
            print(o.groupby(["design", "exit_reason"]).size().unstack(fill_value=0))
            print("\nAvg R by design / exit_reason:")
            print(o.groupby(["design", "exit_reason"])["r_r"].mean().unstack())
            print("\nOverall by design:")
            g = o.groupby("design")["r_r"].agg(["count", "mean", "sum"])
            print(g)
            # SL vs TP hit rates
            for d in o["design"].dropna().unique():
                sub = o[o["design"] == d]
                n = len(sub)
                sl = (sub["exit_reason"] == "SL").sum()
                tp = (sub["exit_reason"] == "TP").sum()
                hz = sub["exit_reason"].isin(["HORIZON", "HORIZON_OR_MANUAL"]).sum()
                print(f"\nDesign {d}: n={n}  SL={sl} ({sl/n:.0%})  TP={tp} ({tp/n:.0%})  HORIZON={hz} ({hz/n:.0%})")
    else:
        print("\nNo outcomes_log.csv yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
