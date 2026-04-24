from tests.unit._indicator_fixtures import linear_uptrend, load_btc_1m_synthetic, noisy_flat
from trading_sandwich.indicators.volatility import (
    compute_atr,
    compute_bollinger,
    compute_donchian,
    compute_keltner,
)


def test_atr_positive_for_real_data():
    df = load_btc_1m_synthetic()
    atr = compute_atr(df["high"], df["low"], df["close"], period=14)
    valid = atr.dropna()
    assert (valid > 0).all()


def test_bollinger_upper_above_lower():
    df = linear_uptrend(n=50)
    upper, middle, lower, width = compute_bollinger(df["close"], period=20, std=2)
    mask = upper.notna()
    assert (upper[mask] >= middle[mask]).all()
    assert (middle[mask] >= lower[mask]).all()
    assert (width[mask] >= 0).all()


def test_bollinger_width_bounded_in_flat():
    df = noisy_flat(n=300)
    _, _, _, width = compute_bollinger(df["close"], period=20, std=2)
    valid = width.dropna()
    assert valid.iloc[-50:].max() < 10.0


def test_keltner_middle_is_ema():
    df = linear_uptrend(n=50)
    upper, middle, lower = compute_keltner(df["high"], df["low"], df["close"], period=20, atr_mult=2)
    # Mask where ALL three series have values (EMA-20 and ATR-14 each have
    # their own warmup; upper/lower only valid once both are populated).
    mask = upper.notna() & middle.notna() & lower.notna()
    assert (middle[mask] <= df["close"][mask]).all()
    assert (upper[mask] > middle[mask]).all()
    assert (lower[mask] < middle[mask]).all()


def test_donchian_upper_is_rolling_max():
    df = linear_uptrend(n=50)
    upper, _middle, _lower = compute_donchian(df["high"], df["low"], period=20)
    expected_upper_30 = df["high"].iloc[11:31].max()
    assert abs(float(upper.iloc[30]) - expected_upper_30) < 1e-6
