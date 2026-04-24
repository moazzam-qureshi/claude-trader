from datetime import UTC, datetime, timedelta

import pandas as pd

from trading_sandwich.indicators.volume import (
    compute_mfi,
    compute_obv,
    compute_volume_zscore,
    compute_vwap_session,
)


def _build(n: int = 50, start_hour: int = 10) -> pd.DataFrame:
    base = datetime(2026, 4, 21, start_hour, 0, tzinfo=UTC)
    rows = []
    for i in range(n):
        close = 100.0 + i * 0.2
        rows.append({
            "close_time": base + timedelta(minutes=i),
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": 10 + i,
        })
    return pd.DataFrame(rows)


def test_obv_monotonic_in_uptrend():
    df = _build()
    obv = compute_obv(df["close"], df["volume"])
    assert (obv.diff().dropna() > 0).all()


def test_vwap_resets_at_midnight_utc():
    base = datetime(2026, 4, 21, 23, 30, tzinfo=UTC)
    rows = []
    for i in range(60):
        close = 100.0 + i * 0.1
        rows.append({
            "close_time": base + timedelta(minutes=i),
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": 10.0,
        })
    df = pd.DataFrame(rows)
    vwap = compute_vwap_session(df)
    assert not vwap.isna().all()
    # At the first post-midnight bar (i=30 = 00:00), VWAP equals that bar's
    # typical price because the session just reset.
    assert abs(float(vwap.iloc[30]) - float(df["close"].iloc[30])) < 0.1


def test_volume_zscore_mean_near_zero_for_stationary_series():
    # z-score on a stationary series should have mean near 0 (by construction).
    import numpy as np
    rng = np.random.default_rng(42)
    vol = pd.Series(100.0 + rng.standard_normal(500) * 10.0)
    z = compute_volume_zscore(vol, window=20)
    valid = z.dropna()
    assert abs(float(valid.mean())) < 0.2


def test_volume_zscore_positive_when_volume_spikes():
    # Trailing volume ~10; current bar volume 100 → strong positive z-score.
    base = [10.0] * 30
    base[-1] = 100.0
    vol = pd.Series(base)
    z = compute_volume_zscore(vol, window=20)
    assert float(z.iloc[-1]) > 3.0


def test_mfi_bounds():
    df = _build(n=50)
    mfi = compute_mfi(df["high"], df["low"], df["close"], df["volume"], period=14)
    valid = mfi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()
