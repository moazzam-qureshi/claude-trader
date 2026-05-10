from trading_sandwich._universe import symbols, timeframes


def test_symbols_from_policy():
    """Phase 3 (spec §6.1): universe is tiered. symbols() returns flat list
    across core+active+observation. Excluded tier is not returned."""
    s = symbols()
    # Core
    assert "BTCUSDT" in s
    assert "ETHUSDT" in s
    assert "SOLUSDT" in s
    # Active (sample)
    assert "AVAXUSDT" in s
    assert "LINKUSDT" in s
    # Observation (sample)
    assert "INJUSDT" in s
    # Excluded — never returned
    assert "SHIBUSDT" not in s     # memecoin
    assert "AAVEUSDT" not in s     # lending
    assert "GMXUSDT" not in s      # perp protocol
    # Sanity: full halal candidate set is sized
    assert len(s) >= 25            # core(3) + active(22) + observation(7) = 32


def test_timeframes_from_policy():
    """timeframes() returns whatever policy.yaml declares."""
    tfs = timeframes()
    assert "5m" in tfs
    assert "1h" in tfs
