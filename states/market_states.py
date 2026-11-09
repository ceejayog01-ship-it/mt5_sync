"""Frozen TSE market-state masks (Phase 15/18/19 thresholds)."""
from __future__ import annotations
import numpy as np
import pandas as pd
from config import VOL_LOOKBACK, TREND_LOOKBACK
from states.thresholds import get_thresholds


def build_market_states(ohlcv: pd.DataFrame, pair: str | None = None) -> dict:
    """Return boolean masks keyed by (family, value). Thresholds are per pair."""
    th, _ = get_thresholds(pair)
    VOL48_P33, VOL48_P67 = th["vol48_p33"], th["vol48_p67"]
    TREND96_P33, TREND96_P67 = th["trend96_p33"], th["trend96_p67"]
    close = ohlcv["close"].astype(float).values
    n = len(close)
    logret = np.full(n, np.nan)
    logret[1:] = np.log(close[1:] / close[:-1])
    vol48 = pd.Series(logret).rolling(VOL_LOOKBACK, min_periods=20).std().values
    trend96 = pd.Series(close).pct_change(TREND_LOOKBACK).values

    if "timestamp" in ohlcv.columns:
        hours = pd.to_datetime(ohlcv["timestamp"], utc=True).dt.hour.values
    else:
        idx = ohlcv.index
        hours = np.asarray(idx.tz_convert("UTC").hour) if getattr(idx, "tz", None) else np.asarray(getattr(idx, "hour", np.full(n, 12)))

    low_vol = np.isfinite(vol48) & (vol48 < VOL48_P33)
    high_vol = np.isfinite(vol48) & (vol48 >= VOL48_P67)
    trend_down = np.isfinite(trend96) & (trend96 < TREND96_P33)
    trend_up = np.isfinite(trend96) & (trend96 >= TREND96_P67)
    asia = (hours >= 0) & (hours < 8)
    london = (hours >= 8) & (hours < 16)
    ny = (hours >= 16) & (hours < 24)

    # Direction proxy (research uses candidate ensembles; momentum is the live stand-in)
    mom = pd.Series(close).pct_change(12).values
    direction_long = np.isfinite(mom) & (mom > 0.0005)
    direction_short = np.isfinite(mom) & (mom < -0.0005)

    return {
        "low_vol": low_vol,
        "high_vol": high_vol,
        "trend_down": trend_down,
        "trend_up": trend_up,
        "asia": asia,
        "london": london,
        "ny": ny,
        "trend_down_ny": trend_down & ny,
        "high_vol_trend_down": high_vol & trend_down,
        "low_vol_trend_up": low_vol & trend_up,
        "high_vol_ny": high_vol & ny,
        "direction_long": direction_long,
        "direction_short": direction_short,
    }


def atr_wilder(ohlcv: pd.DataFrame, period: int = 14) -> np.ndarray:
    h = ohlcv["high"].astype(float).values
    l = ohlcv["low"].astype(float).values
    c = ohlcv["close"].astype(float).values
    prev = np.roll(c, 1)
    prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().values
