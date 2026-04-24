"""Derived inputs consumed by the regime classifier. Pure functions; no
dependency on raw-market tables beyond the indicator Series they take as input.
"""
from __future__ import annotations

import pandas as pd


def compute_ema_slope_bps(ema: pd.Series, window: int = 10) -> pd.Series:
    """Slope of EMA over `window` bars, expressed in basis points per bar
    relative to the current EMA value. Positive = rising.
    """
    delta = ema - ema.shift(window)
    slope_bps_total = (delta / ema) * 10_000.0
    return (slope_bps_total / window).rename("ema_slope_bps")


def compute_atr_percentile(atr: pd.Series, window: int = 100) -> pd.Series:
    """Rolling-window percentile rank of current ATR (0-100)."""
    return (
        atr.rolling(window=window).rank(pct=True) * 100.0
    ).rename(f"atr_percentile_{window}")


def compute_bb_width_percentile(bb_width: pd.Series, window: int = 100) -> pd.Series:
    """Rolling-window percentile rank of current BB-width (0-100)."""
    return (
        bb_width.rolling(window=window).rank(pct=True) * 100.0
    ).rename(f"bb_width_percentile_{window}")
