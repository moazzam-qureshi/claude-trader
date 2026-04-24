from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.range_rejection import detect_range_rejection


def _stamp_regime_and_donchian(rows, trend="range", vol="normal", up=Decimal("110"), lo=Decimal("95")):
    for i in range(len(rows)):
        rows[i] = rows[i].model_copy(update={
            "donchian_upper": up, "donchian_lower": lo,
            "trend_regime": trend, "vol_regime": vol,
        })


def test_fires_on_range_low_rejection():
    rows = make_features_series(n=60, close_slope=0.0, atr=1.0)
    _stamp_regime_and_donchian(rows)
    # Most recent bar wicked below Donchian lower (95) but closed back inside
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("97"),
        "swing_high_5": Decimal("105"),
        "swing_low_5": Decimal("94.5"),
    })
    s = detect_range_rejection(rows)
    assert s is not None
    assert s.direction == "long"
    assert s.archetype == "range_rejection"


def test_fires_on_range_high_rejection():
    rows = make_features_series(n=60, close_slope=0.0, atr=1.0)
    _stamp_regime_and_donchian(rows)
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("108"),
        "swing_high_5": Decimal("110.5"),
        "swing_low_5": Decimal("105"),
    })
    s = detect_range_rejection(rows)
    assert s is not None
    assert s.direction == "short"


def test_no_fire_in_trend_regime():
    rows = make_features_series(n=60, close_slope=0.0, atr=1.0)
    _stamp_regime_and_donchian(rows, trend="trend_up")
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("97"),
        "swing_high_5": Decimal("105"),
        "swing_low_5": Decimal("94.5"),
    })
    assert detect_range_rejection(rows) is None
