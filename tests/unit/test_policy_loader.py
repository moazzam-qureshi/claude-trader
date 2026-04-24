from decimal import Decimal

from trading_sandwich._policy import (
    get_confidence_threshold,
    get_cooldown_minutes,
    get_dedup_window_minutes,
    get_funding_threshold,
    get_regime_thresholds,
    load_policy,
)


def test_load_policy_returns_dict():
    p = load_policy()
    assert isinstance(p, dict)
    assert p["trading_enabled"] is False


def test_get_confidence_threshold():
    assert get_confidence_threshold("trend_pullback") == Decimal("0.70")
    assert get_confidence_threshold("divergence_rsi") == Decimal("0.65")


def test_get_cooldown_minutes():
    assert get_cooldown_minutes("trend_pullback") == 30
    assert get_cooldown_minutes("funding_extreme") == 120


def test_get_dedup_window_minutes():
    assert get_dedup_window_minutes() == 30


def test_get_regime_thresholds():
    r = get_regime_thresholds()
    assert r["trend_slope_threshold_bps"] == 2.0
    assert r["adx_trend_threshold"] == 20
    assert r["squeeze_percentile"] == 20
    assert r["expansion_percentile"] == 80


def test_get_funding_threshold_known_symbol():
    long_, short_ = get_funding_threshold("BTCUSDT")
    assert long_ == Decimal("-0.0003")
    assert short_ == Decimal("0.0003")


def test_get_funding_threshold_unknown_falls_back_to_default():
    long_, short_ = get_funding_threshold("NOTINUNIVERSE")
    assert long_ == Decimal("-0.0005")
    assert short_ == Decimal("0.0005")
