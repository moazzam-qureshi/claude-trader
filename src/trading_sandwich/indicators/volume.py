"""Volume + flow indicators. VWAP is session-anchored (00:00 UTC daily reset)."""
from __future__ import annotations

import pandas as pd
import talib


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    out = talib.OBV(close.to_numpy(dtype=float), volume.to_numpy(dtype=float))
    return pd.Series(out, index=close.index, name="obv")


def compute_vwap_session(candles: pd.DataFrame) -> pd.Series:
    """VWAP that resets at 00:00 UTC every day. Input DataFrame must contain
    'close_time' (tz-aware), 'high', 'low', 'close', 'volume'.
    """
    df = candles.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    session = df["close_time"].dt.floor("D")
    cum_vp = (typical * df["volume"]).groupby(session).cumsum()
    cum_vol = df["volume"].groupby(session).cumsum()
    vwap = cum_vp / cum_vol
    vwap.index = df.index
    return vwap.rename("vwap")


def compute_volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
    mean = volume.rolling(window=window).mean()
    std = volume.rolling(window=window).std()
    z = (volume - mean) / std.replace(0, pd.NA)
    return z.rename(f"volume_zscore_{window}")


def compute_mfi(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    out = talib.MFI(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float),
        close.to_numpy(dtype=float), volume.to_numpy(dtype=float),
        timeperiod=period,
    )
    return pd.Series(out, index=high.index, name=f"mfi_{period}")
