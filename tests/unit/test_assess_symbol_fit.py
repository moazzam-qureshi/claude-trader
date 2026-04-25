from trading_sandwich.mcp.tools.universe import assess_against_hard_limits


HARD_LIMITS = {
    "min_24h_volume_usd_floor": 100_000_000,
    "vol_30d_annualized_max_ceiling": 3.0,
}


def test_passes_when_metrics_in_range():
    res = assess_against_hard_limits(
        symbol="SUIUSDT",
        volume_24h_usd=300_000_000,
        vol_30d_annualized=1.5,
        hard_limits=HARD_LIMITS,
    )
    assert res["structural"]["passes"] is True
    assert res["liquidity"]["passes"] is True


def test_fails_below_volume_floor():
    res = assess_against_hard_limits(
        symbol="SUIUSDT",
        volume_24h_usd=50_000_000,
        vol_30d_annualized=1.5,
        hard_limits=HARD_LIMITS,
    )
    assert res["liquidity"]["passes"] is False
    assert "min_24h_volume_usd_floor" in res["liquidity"]["failed_criteria"]


def test_fails_above_vol_ceiling():
    res = assess_against_hard_limits(
        symbol="SUIUSDT",
        volume_24h_usd=300_000_000,
        vol_30d_annualized=4.0,
        hard_limits=HARD_LIMITS,
    )
    assert res["liquidity"]["passes"] is False
    assert "vol_30d_annualized_max_ceiling" in res["liquidity"]["failed_criteria"]
