"""
Evaluate the 51 BEST research strategies (AGREE of two parent edges).

Market states (VOL / TREND / SESSION / INTERACTION) use frozen TSE thresholds.
DIRECTION / STRENGTH / PERSISTENCE use a causal momentum proxy as the
candidate-signal stand-in (full alpha materializer is optional later).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from states.market_states import build_market_states, atr_wilder

DATA = Path(__file__).resolve().parents[1] / "data"
DIRECTION_THRESHOLD = 0.01


def _load_json(name: str):
    with open(DATA / name) as f:
        return json.load(f)


def load_best51() -> List[Dict]:
    path = DATA / "strategies.json"
    rows = _load_json("strategies.json")
    # strategies.json in TSE is already the 51 BEST list
    return rows


def load_edges() -> Dict[str, Dict]:
    return _load_json("edges.json")


def parse_edge_id(edge_id: str) -> Tuple[str, str, str, int]:
    """
    STR_CAND_0047_DIRECTION_LONG_h48
    → candidate=0047, family=DIRECTION, value=LONG, horizon=48
    """
    m = re.match(
        r"STR_CAND_(\d+)_(DIRECTION|STRENGTH|PERSISTENCE|VOLATILITY|TREND|SESSION|INTERACTION)_(.+)_h(\d+)$",
        edge_id,
    )
    if not m:
        # try edges.json fallback later
        return "", "", "", 48
    return m.group(1), m.group(2), m.group(3), int(m.group(4))


def candidate_proxy_signal(close: np.ndarray) -> np.ndarray:
    """Causal momentum proxy in [-1, 1] — stand-in for ensemble candidate signal."""
    s = pd.Series(close.astype(float))
    mom = s.pct_change(12)  # ~6h on M30
    z = (mom - mom.rolling(48, min_periods=20).mean()) / (mom.rolling(48, min_periods=20).std() + 1e-12)
    sig = np.tanh(z.fillna(0).values)
    return sig


def candidate_masks(sig: np.ndarray) -> Dict[Tuple[str, str], np.ndarray]:
    abs_s = np.abs(sig)
    active = abs_s > DIRECTION_THRESHOLD
    d_long = sig > DIRECTION_THRESHOLD
    d_short = sig < -DIRECTION_THRESHOLD
    d_flat = ~active

    s = pd.Series(abs_s)
    act = pd.Series(active)
    q33 = s.where(act).expanding(min_periods=40).quantile(0.33)
    q67 = s.where(act).expanding(min_periods=40).quantile(0.67)
    weak = active & (abs_s <= q33.fillna(np.inf).values)
    medium = active & (abs_s > q33.fillna(0).values) & (abs_s <= q67.fillna(np.inf).values)
    strong = active & (abs_s > q67.fillna(0).values)

    prev_active = np.roll(active, 1)
    prev_active[0] = False
    prev_sig = np.roll(sig, 1)
    prev_sig[0] = 0.0
    new = active & ~prev_active
    persistent = active & prev_active & (np.sign(sig) == np.sign(prev_sig))
    flip = active & prev_active & (np.sign(sig) != np.sign(prev_sig)) & (np.abs(prev_sig) > DIRECTION_THRESHOLD)

    return {
        ("DIRECTION", "LONG"): d_long,
        ("DIRECTION", "SHORT"): d_short,
        ("DIRECTION", "FLAT"): d_flat,
        ("STRENGTH", "WEAK"): weak,
        ("STRENGTH", "MEDIUM"): medium,
        ("STRENGTH", "STRONG"): strong,
        ("PERSISTENCE", "NEW"): new,
        ("PERSISTENCE", "PERSISTENT"): persistent,
        ("PERSISTENCE", "FLIP"): flip,
    }


def edge_active(
    edge_id: str,
    edges: Dict[str, Dict],
    market: Dict[str, np.ndarray],
    cand: Dict[Tuple[str, str], np.ndarray],
    sig: np.ndarray,
) -> Tuple[np.ndarray, str]:
    """
    Return boolean mask where this parent edge is active, and implied direction
    ('BUY' / 'SELL' / '').
    """
    meta = edges.get(edge_id, {})
    cand_id, family, value, _ = parse_edge_id(edge_id)
    if meta:
        family = meta.get("state_family", family)
        value = meta.get("state_value", value)
        direction = str(meta.get("direction", "")).upper()
    else:
        direction = value if family == "DIRECTION" else ""

    n = len(sig)
    key = (family, value)

    # market-state families
    market_map = {
        ("VOLATILITY", "LOW_VOL"): market.get("low_vol"),
        ("VOLATILITY", "HIGH_VOL"): market.get("high_vol"),
        ("TREND", "TREND_DOWN"): market.get("trend_down"),
        ("TREND", "TREND_UP"): market.get("trend_up"),
        ("SESSION", "ASIA"): market.get("asia"),
        ("SESSION", "LONDON"): market.get("london"),
        ("SESSION", "NY"): market.get("ny"),
        ("INTERACTION", "HIGH_VOL_TREND_DOWN"): market.get("high_vol_trend_down"),
        ("INTERACTION", "TREND_DOWN_NY"): market.get("trend_down_ny"),
        ("INTERACTION", "LOW_VOL_TREND_UP"): market.get("low_vol_trend_up"),
        ("INTERACTION", "HIGH_VOL_NY"): market.get("high_vol_ny"),
    }

    if key in market_map and market_map[key] is not None:
        mask = market_map[key] & (np.abs(sig) > DIRECTION_THRESHOLD)
        # direction from candidate signal sign when state is regime-only
        if not direction:
            direction = "LONG" if float(np.nanmean(sig[-5:])) > 0 else "SHORT"
        return mask, ("BUY" if direction == "LONG" else "SELL" if direction == "SHORT" else "")

    if key in cand:
        mask = cand[key]
        if family == "DIRECTION":
            return mask, ("BUY" if value == "LONG" else "SELL" if value == "SHORT" else "")
        # strength / persistence: direction from signal sign
        side_mask_long = mask & (sig > DIRECTION_THRESHOLD)
        side_mask_short = mask & (sig < -DIRECTION_THRESHOLD)
        # return combined; caller uses sign of sig
        return mask & active_sign(sig), ""

    return np.zeros(n, dtype=bool), ""


def active_sign(sig: np.ndarray) -> np.ndarray:
    return np.abs(sig) > DIRECTION_THRESHOLD


def evaluate_strategies(ohlcv: pd.DataFrame, pair: str | None = None) -> List[Dict]:
    """
    On the last completed bar, return list of fired BEST strategies:
      {strategy_id, rank, side, pattern/plain, oos_net, horizon_bars, ...}
    """
    strategies = load_best51()
    edges = load_edges()
    close = ohlcv["close"].astype(float).values
    sig = candidate_proxy_signal(close)
    market = build_market_states(ohlcv, pair)
    cand = candidate_masks(sig)
    i = len(ohlcv) - 1
    fired: List[Dict] = []

    for rank, strat in enumerate(strategies, start=1):
        pa = strat["parent_a"]
        pb = strat["parent_b"]
        mask_a, dir_a = edge_active(pa, edges, market, cand, sig)
        mask_b, dir_b = edge_active(pb, edges, market, cand, sig)

        if not (mask_a[i] and mask_b[i]):
            continue

        # AGREE: same sign on candidate proxy
        sa, sb = sig[i], sig[i]  # same underlying proxy; require active + consistent side
        if abs(sa) <= DIRECTION_THRESHOLD:
            continue

        # Resolve side: prefer explicit DIRECTION parent, else signal sign
        side = dir_a or dir_b
        if not side:
            side = "BUY" if sa > 0 else "SELL"
        # If both parents declare direction, they must match
        if dir_a and dir_b and dir_a != dir_b:
            continue
        if dir_a and ((dir_a == "BUY" and sa < 0) or (dir_a == "SELL" and sa > 0)):
            continue
        if dir_b and ((dir_b == "BUY" and sa < 0) or (dir_b == "SELL" and sa > 0)):
            continue

        fired.append({
            "strategy_id": strat["strategy_id"],
            "rank": rank,
            "side": side,
            "tag": strat.get("tag", "BEST"),
            "plain_a": strat.get("plain_a", pa),
            "plain_b": strat.get("plain_b", pb),
            "parent_a": pa,
            "parent_b": pb,
            "oos_net": float(strat.get("oos_net") or 0),
            "horizon_bars": int(strat.get("horizon_bars") or 48),
            "base_candidates": strat.get("base_candidates", ""),
            "pattern": f"BEST_R{rank:02d}",
        })

    return fired
