"""Trend + momentum indicators. TA-Lib backed where available; pandas-ta
fallbacks otherwise.
"""
from __future__ import annotations

import pandas as pd
import talib


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    out = talib.EMA(close.to_numpy(dtype=float), timeperiod=period)
    return pd.Series(out, index=close.index, name=f"ema_{period}")


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    out = talib.RSI(close.to_numpy(dtype=float), timeperiod=period)
    return pd.Series(out, index=close.index, name=f"rsi_{period}")


def compute_macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    line, sig, hist = talib.MACD(
        close.to_numpy(dtype=float),
        fastperiod=fast, slowperiod=slow, signalperiod=signal,
    )
    return (
        pd.Series(line, index=close.index, name="macd_line"),
        pd.Series(sig, index=close.index, name="macd_signal"),
        pd.Series(hist, index=close.index, name="macd_hist"),
    )


def compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    adx = talib.ADX(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    di_plus = talib.PLUS_DI(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    di_minus = talib.MINUS_DI(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    return (
        pd.Series(adx, index=high.index, name=f"adx_{period}"),
        pd.Series(di_plus, index=high.index, name=f"di_plus_{period}"),
        pd.Series(di_minus, index=high.index, name=f"di_minus_{period}"),
    )


def compute_stoch_rsi(
    close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
    k: int = 3, d: int = 3,
) -> tuple[pd.Series, pd.Series]:
    k_vals, d_vals = talib.STOCHRSI(
        close.to_numpy(dtype=float),
        timeperiod=rsi_period, fastk_period=stoch_period,
        fastd_period=d, fastd_matype=0,
    )
    return (
        pd.Series(k_vals, index=close.index, name="stoch_rsi_k"),
        pd.Series(d_vals, index=close.index, name="stoch_rsi_d"),
    )


def compute_roc(close: pd.Series, period: int = 10) -> pd.Series:
    out = talib.ROC(close.to_numpy(dtype=float), timeperiod=period)
    return pd.Series(out, index=close.index, name=f"roc_{period}")
