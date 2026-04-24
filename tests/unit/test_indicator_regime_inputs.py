from tests.unit._indicator_fixtures import linear_uptrend, noisy_flat
from trading_sandwich.indicators.regime_inputs import (
    compute_atr_percentile,
    compute_bb_width_percentile,
    compute_ema_slope_bps,
)


def test_ema_slope_positive_in_uptrend():
    df = linear_uptrend(n=100)
    from trading_sandwich.indicators.trend import compute_ema
    ema = compute_ema(df["close"], period=21)
    slope = compute_ema_slope_bps(ema, window=10)
    valid = slope.dropna()
    assert valid.iloc[-1] > 0


def test_atr_percentile_bounded_0_100():
    df = linear_uptrend(n=300)
    from trading_sandwich.indicators.volatility import compute_atr
    atr = compute_atr(df["high"], df["low"], df["close"], period=14)
    pct = compute_atr_percentile(atr, window=100)
    valid = pct.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_bb_width_percentile_bounded_0_100():
    df = linear_uptrend(n=300)
    from trading_sandwich.indicators.volatility import compute_bollinger
    _, _, _, width = compute_bollinger(df["close"], period=20, std=2)
    pct = compute_bb_width_percentile(width, window=100)
    valid = pct.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_bb_width_percentile_low_in_flat():
    df = noisy_flat(n=300)
    from trading_sandwich.indicators.volatility import compute_bollinger
    _, _, _, width = compute_bollinger(df["close"], period=20, std=2)
    pct = compute_bb_width_percentile(width, window=100)
    valid = pct.dropna()
    # In a stationary series percentile average should sit around 50; test we
    # stay in the broadly-bounded middle band on the recent window.
    assert float(valid.iloc[-50:].mean()) < 70
