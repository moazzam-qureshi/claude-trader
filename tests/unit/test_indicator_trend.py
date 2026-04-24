from tests.unit._indicator_fixtures import linear_uptrend, load_btc_1m_synthetic
from trading_sandwich.indicators.trend import (
    compute_adx,
    compute_ema,
    compute_macd,
    compute_roc,
    compute_rsi,
    compute_stoch_rsi,
)


def test_ema_matches_period_sma_at_warmup():
    df = load_btc_1m_synthetic()
    ema = compute_ema(df["close"], period=21)
    assert ema.iloc[:20].isna().all()
    sma21 = df["close"].iloc[:21].mean()
    assert abs(float(ema.iloc[20]) - sma21) < 0.01


def test_ema_length_matches_input():
    df = linear_uptrend(n=250)
    ema = compute_ema(df["close"], period=200)
    assert len(ema) == 250


def test_macd_returns_three_series_same_length():
    df = linear_uptrend(n=300)
    line, signal, hist = compute_macd(df["close"])
    assert len(line) == len(signal) == len(hist) == 300
    # TA-Lib MACD needs slow+signal-1 = 34 bars of warmup before line/signal are valid
    assert line.iloc[:25].isna().all()
    assert line.iloc[34:].notna().all()


def test_adx_positive_in_trend():
    df = linear_uptrend(n=100)
    adx, di_plus, di_minus = compute_adx(df["high"], df["low"], df["close"], period=14)
    valid = adx.dropna()
    assert (valid.iloc[-10:] > 25).all()
    assert (di_plus.iloc[-10:] > di_minus.iloc[-10:]).all()


def test_rsi_bounds():
    df = load_btc_1m_synthetic()
    rsi = compute_rsi(df["close"], period=14)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_stoch_rsi_bounds():
    df = linear_uptrend(n=100)
    k, d = compute_stoch_rsi(df["close"], rsi_period=14, stoch_period=14, k=3, d=3)
    for series in (k, d):
        valid = series.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()


def test_roc_on_linear_uptrend():
    df = linear_uptrend(n=100)
    roc = compute_roc(df["close"], period=10)
    valid = roc.dropna()
    assert (valid > 0).all()
