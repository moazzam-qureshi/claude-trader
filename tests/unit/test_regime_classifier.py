from decimal import Decimal

from trading_sandwich.regime.classifier import classify

_POLICY = {
    "trend_slope_threshold_bps": 2.0,
    "adx_trend_threshold": 20,
    "squeeze_percentile": 20,
    "expansion_percentile": 80,
}


def test_trend_up_strict():
    trend, vol = classify(
        close=Decimal("101"), ema_55=Decimal("100"),
        ema_slope_bps=3.0, adx=25.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "trend_up"
    assert vol == "normal"


def test_trend_down_strict():
    trend, _ = classify(
        close=Decimal("99"), ema_55=Decimal("100"),
        ema_slope_bps=-3.0, adx=25.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "trend_down"


def test_range_when_adx_below_threshold():
    trend, _ = classify(
        close=Decimal("101"), ema_55=Decimal("100"),
        ema_slope_bps=3.0, adx=15.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "range"


def test_range_when_slope_below_threshold():
    trend, _ = classify(
        close=Decimal("101"), ema_55=Decimal("100"),
        ema_slope_bps=1.0,
        adx=25.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "range"


def test_squeeze_vol_regime():
    _, vol = classify(
        close=Decimal("100"), ema_55=Decimal("100"),
        ema_slope_bps=0.0, adx=15.0,
        bb_width_percentile_100=10.0,
        policy=_POLICY,
    )
    assert vol == "squeeze"


def test_expansion_vol_regime():
    _, vol = classify(
        close=Decimal("100"), ema_55=Decimal("100"),
        ema_slope_bps=0.0, adx=15.0,
        bb_width_percentile_100=85.0,
        policy=_POLICY,
    )
    assert vol == "expansion"


def test_returns_range_normal_when_any_input_none():
    trend, vol = classify(
        close=Decimal("100"), ema_55=None,
        ema_slope_bps=None, adx=None,
        bb_width_percentile_100=None,
        policy=_POLICY,
    )
    assert (trend, vol) == ("range", "normal")
