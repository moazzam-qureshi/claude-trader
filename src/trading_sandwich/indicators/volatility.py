"""Volatility + range indicators."""
from __future__ import annotations

import pandas as pd
import talib


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> pd.Series:
    out = talib.ATR(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    return pd.Series(out, index=high.index, name=f"atr_{period}")


def compute_bollinger(
    close: pd.Series, period: int = 20, std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    upper, middle, lower = talib.BBANDS(
        close.to_numpy(dtype=float),
        timeperiod=period, nbdevup=std, nbdevdn=std, matype=0,
    )
    width = upper - lower
    return (
        pd.Series(upper, index=close.index, name="bb_upper"),
        pd.Series(middle, index=close.index, name="bb_middle"),
        pd.Series(lower, index=close.index, name="bb_lower"),
        pd.Series(width, index=close.index, name="bb_width"),
    )


def compute_keltner(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 20, atr_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = talib.EMA(close.to_numpy(dtype=float), timeperiod=period)
    atr = talib.ATR(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    upper = middle + atr * atr_mult
    lower = middle - atr * atr_mult
    return (
        pd.Series(upper, index=close.index, name="keltner_upper"),
        pd.Series(middle, index=close.index, name="keltner_middle"),
        pd.Series(lower, index=close.index, name="keltner_lower"),
    )


def compute_donchian(
    high: pd.Series, low: pd.Series, period: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    upper = high.rolling(window=period).max()
    lower = low.rolling(window=period).min()
    middle = (upper + lower) / 2.0
    return (
        upper.rename("donchian_upper"),
        middle.rename("donchian_middle"),
        lower.rename("donchian_lower"),
    )
