"""Price-structure features: swing H/L (fractal), classic pivots, prior-day/week H/L."""
from __future__ import annotations

import pandas as pd


def compute_swing_high_low(
    high: pd.Series, low: pd.Series, lookback: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """Most recent confirmed swing H/L using an N-bar fractal. A bar is a swing
    high if its high is the unique max over the `lookback`-bar window centred on
    it. Forward-fill so the most recent confirmed swing H/L is carried until a
    new one appears.
    """
    half = (lookback - 1) // 2
    swing_high = pd.Series(index=high.index, dtype=float)
    swing_low = pd.Series(index=low.index, dtype=float)
    for i in range(half, len(high) - half):
        window_h = high.iloc[i - half: i + half + 1]
        window_l = low.iloc[i - half: i + half + 1]
        if float(high.iloc[i]) == float(window_h.max()) and (window_h == window_h.max()).sum() == 1:
            swing_high.iloc[i] = float(high.iloc[i])
        if float(low.iloc[i]) == float(window_l.min()) and (window_l == window_l.min()).sum() == 1:
            swing_low.iloc[i] = float(low.iloc[i])
    return (
        swing_high.ffill().rename(f"swing_high_{lookback}"),
        swing_low.ffill().rename(f"swing_low_{lookback}"),
    )


def compute_classic_pivots(
    high: float, low: float, close: float,
) -> tuple[float, float, float, float, float]:
    """Classic floor-trader pivots for one trading session."""
    p = (high + low + close) / 3.0
    r1 = 2.0 * p - low
    s1 = 2.0 * p - high
    r2 = p + (high - low)
    s2 = p - (high - low)
    return p, r1, r2, s1, s2


def compute_prior_day_hl(candles: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """At each candle, the high/low of the UTC day preceding the candle's close_time.
    Forward-filled across the day. Input must have 'close_time' (tz-aware) + 'high' + 'low'.
    """
    df = candles[["close_time", "high", "low"]].copy()
    df["day"] = df["close_time"].dt.floor("D")
    daily = df.groupby("day").agg(day_high=("high", "max"), day_low=("low", "min")).reset_index()
    daily["prior_day_high"] = daily["day_high"].shift(1)
    daily["prior_day_low"] = daily["day_low"].shift(1)
    merged = df.merge(daily[["day", "prior_day_high", "prior_day_low"]], on="day", how="left")
    merged.index = df.index
    return merged["prior_day_high"].rename("prior_day_high"), merged["prior_day_low"].rename("prior_day_low")


def compute_prior_week_hl(candles: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Prior ISO-week high/low, forward-filled across the week."""
    df = candles[["close_time", "high", "low"]].copy()
    # Pandas W-SUN = weeks ending on Sunday → Monday-through-Sunday weeks.
    # W-MON is the opposite (weeks ending on Monday), which off-by-ones at
    # every Monday. Using W-SUN gives "prior week" = Mon..Sun of the week
    # strictly preceding the bar's close_time.
    df["week"] = df["close_time"].dt.to_period("W-SUN").dt.start_time.dt.tz_localize("UTC")
    weekly = df.groupby("week").agg(week_high=("high", "max"), week_low=("low", "min")).reset_index()
    weekly["prior_week_high"] = weekly["week_high"].shift(1)
    weekly["prior_week_low"] = weekly["week_low"].shift(1)
    merged = df.merge(weekly[["week", "prior_week_high", "prior_week_low"]], on="week", how="left")
    merged.index = df.index
    return merged["prior_week_high"].rename("prior_week_high"), merged["prior_week_low"].rename("prior_week_low")
