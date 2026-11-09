"""
TSE + KimiBot Executor - configuration
All secrets come from environment variables (GitHub Actions secrets). No .env file.

Two designs run simultaneously on every signal:
  A) Research  - SL = 2 x ATR(M30), no TP, closed after HORIZON_BARS (48 x M30 = 24h)
  B) Hybrid    - SL = 2 x ATR(M30), TP = the strategy's research net return (in price terms)
Hourly management (both designs): the stop is tightened by +1 ATR(M30) in the trade
direction, never beyond a valid distance from the live price, never widening risk.
"""
import os
from typing import List


def _csv(name: str, default: str) -> List[str]:
    return [p.strip().upper() for p in os.getenv(name, default).split(",") if p.strip()]


# -- Pairs -------------------------------------------------------------------
# Scanned for signals (Telegram). Override with the KB_PAIRS repo variable.
DEFAULT_PAIRS: List[str] = _csv("KB_PAIRS", "XAUUSD,EURUSD,GBPUSD,USDJPY,AUDUSD")
# Pairs whose signals are handed to the MT5 executor. The 51 strategies and their frozen
# thresholds were researched on EURUSD only, so it is the only pair auto-traded by default.
EXEC_PAIRS: List[str] = _csv("KB_EXEC_PAIRS", "EURUSD")

# -- Sessions (UTC / GMT) ----------------------------------------------------
SESSION_LONDON_START = os.getenv("SESSION_LONDON_START", "07:00")
SESSION_LONDON_END = os.getenv("SESSION_LONDON_END", "16:00")
SESSION_NY_START = os.getenv("SESSION_NY_START", "12:00")
SESSION_NY_END = os.getenv("SESSION_NY_END", "21:00")

# -- Research contract -------------------------------------------------------
HORIZON_BARS = int(os.getenv("KB_HORIZON_BARS", "48"))                     # 48 x M30 = 24h
AVG_RESEARCH_NET = float(os.getenv("KB_AVG_RESEARCH_NET", "0.0013085"))    # mean OOS net (return)
MEDIAN_RESEARCH_NET = float(os.getenv("KB_MEDIAN_RESEARCH_NET", "0.0010455"))
ENABLED_DESIGNS = _csv("KB_DESIGNS", "A,B")

# -- Risk --------------------------------------------------------------------
ATR_PERIOD = 14
SL_ATR_MULT = float(os.getenv("KB_SL_ATR_MULT", "2.0"))
HOURLY_ATR_MULT = float(os.getenv("KB_HOURLY_ATR_MULT", "1.0"))
EXEC_TIMEFRAME = "M30"

# -- Filters / ops -----------------------------------------------------------
COOLDOWN_MINUTES = int(os.getenv("KB_COOLDOWN_MINUTES", "30"))
STATE_DIR = os.getenv("KB_STATE_DIR", "state")
MAX_AGE_MIN = float(os.getenv("MAX_AGE_MIN", "25"))
MAX_BAR_AGE_MIN = float(os.getenv("KB_MAX_BAR_AGE_MIN", "75"))   # skip a pair whose last closed M30 bar is older

# -- Frozen EURUSD state thresholds (TRAIN 2023 - do not refit) --------------
# Used for any pair without its own entry in data/pair_thresholds.json.
VOL48_P33 = 0.000541804
VOL48_P67 = 0.000680658
TREND96_P33 = -0.00148102
TREND96_P67 = 0.00154548
VOL_LOOKBACK = 48
TREND_LOOKBACK = 96
