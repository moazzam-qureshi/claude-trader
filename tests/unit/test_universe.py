from trading_sandwich._universe import symbols, timeframes


def test_symbols_from_policy():
    s = symbols()
    assert "BTCUSDT" in s
    assert "ETHUSDT" in s
    assert "SOLUSDT" in s
    assert len(s) == 8


def test_timeframes_from_policy():
    tfs = timeframes()
    assert tfs == ["5m", "15m", "1h", "4h", "1d"]
    assert "1m" not in tfs
