"""Pure feature computation. One function per indicator. Input: pandas Series/DataFrame.
Output: Series (same length, NaN-padded at warmup).

Phase 0 scope: EMA, RSI, ATR. Phase 1 extends to full stack.
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average with Wilder-style warmup (NaN for first period-1)."""
    out = ta.ema(close, length=period)
    out.iloc[: period - 1] = pd.NA
    return out


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI via pandas-ta (uses RMA smoothing)."""
    return ta.rsi(close, length=period)


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR via pandas-ta."""
    return ta.atr(high=high, low=low, close=close, length=period)
