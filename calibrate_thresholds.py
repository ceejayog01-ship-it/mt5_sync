#!/usr/bin/env python3
"""
Compute a pair's volatility / trend state thresholds the same way the frozen EURUSD ones were built
(33rd and 67th percentile over a TRAIN period) and store them in data/pair_thresholds.json.

    python calibrate_thresholds.py --csv GBPUSD_M30.csv --pair GBPUSD --from 2023-01-01 --to 2023-12-31
    python calibrate_thresholds.py --csv ... --pair USDJPY --tz Australia/Sydney   # if timestamps are not UTC

Only the TRAIN period should be used (never the OOS year), otherwise the thresholds leak the future.
Calibrating thresholds does NOT make the 51 strategies valid for that pair: they were selected on EURUSD.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from config import VOL_LOOKBACK, TREND_LOOKBACK

OUT = Path(__file__).resolve().parent / "data" / "pair_thresholds.json"


def load(path: str, tz: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip().lower() for c in df.columns]
    for name in ("timestamp_utc", "timestamp", "datetime", "date", "time"):
        if name in df.columns:
            df = df.rename(columns={name: "timestamp"})
            break
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC") if tz.upper() == "UTC" else \
            ts.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    df["timestamp"] = ts
    return df.dropna(subset=["timestamp", "close"]).sort_values("timestamp").reset_index(drop=True)


def compute(df: pd.DataFrame) -> dict:
    close = df["close"].astype(float).values
    logret = np.full(len(close), np.nan)
    logret[1:] = np.log(close[1:] / close[:-1])
    vol48 = pd.Series(logret).rolling(VOL_LOOKBACK, min_periods=20).std().values
    trend96 = pd.Series(close).pct_change(TREND_LOOKBACK).values
    v = vol48[np.isfinite(vol48)]
    t = trend96[np.isfinite(trend96)]
    if len(v) < 500 or len(t) < 500:
        sys.exit(f"only {len(v)} usable bars; need at least ~500 M30 bars of TRAIN data")
    return {
        "vol48_p33": float(np.percentile(v, 33)), "vol48_p67": float(np.percentile(v, 67)),
        "trend96_p33": float(np.percentile(t, 33)), "trend96_p67": float(np.percentile(t, 67)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--pair", required=True)
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--tz", default="UTC")
    ap.add_argument("--write", action="store_true", help="save into data/pair_thresholds.json (default: just print)")
    a = ap.parse_args()

    df = load(a.csv, a.tz)
    if a.date_from:
        df = df[df["timestamp"] >= pd.Timestamp(a.date_from, tz="UTC")]
    if a.date_to:
        df = df[df["timestamp"] <= pd.Timestamp(a.date_to, tz="UTC")]
    th = compute(df)
    th["source"] = f"{a.csv.split('/')[-1]} {df['timestamp'].iloc[0]:%Y-%m-%d}..{df['timestamp'].iloc[-1]:%Y-%m-%d}, {len(df)} bars"
    print(json.dumps({a.pair.upper(): th}, indent=2))
    if a.write:
        table = json.loads(OUT.read_text()) if OUT.exists() else {}
        table[a.pair.upper()] = th
        OUT.write_text(json.dumps(table, indent=2))
        print(f"saved to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
