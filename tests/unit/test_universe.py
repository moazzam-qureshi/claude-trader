from trading_sandwich._universe import symbols, timeframes


def test_symbols_from_policy():
    """Phase 2.7+: universe is tiered. symbols() returns flat list across
    core+watchlist+observation. Excluded tier is not returned."""
    s = symbols()
    assert "BTCUSDT" in s
    assert "ETHUSDT" in s
    assert "SOLUSDT" in s
    # Core (2) + watchlist (2) + observation (2) = 6. Excluded not included.
    assert len(s) == 6
    assert "SHIBUSDT" not in s  # excluded tier
    assert "PEPEUSDT" not in s


def test_timeframes_from_policy():
    tfs = timeframes()
    assert tfs == ["5m", "15m", "1h", "4h", "1d"]
    assert "1m" not in tfs
