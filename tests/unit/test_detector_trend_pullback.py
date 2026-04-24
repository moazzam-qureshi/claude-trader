from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.trend_pullback import detect_trend_pullback


def test_fires_on_clean_pullback():
    rows = make_features_series(
        n=35, close_slope=0.5, rsi_values=[45] * 30 + [35] * 3 + [42] * 2,
    )
    rows[-1] = rows[-1].model_copy(update={
        "close_price": rows[-2].close_price + Decimal("1.5"),
        "rsi_14": Decimal("42"),
        "ema_21": rows[-1].close_price - Decimal("0.5"),
    })
    rows[-2] = rows[-2].model_copy(update={
        "rsi_14": Decimal("35"),
        "close_price": rows[-2].ema_21,
    })
    rows[-3] = rows[-3].model_copy(update={"rsi_14": Decimal("38")})

    signal = detect_trend_pullback(rows)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.confidence > Decimal("0.5")
    assert signal.archetype == "trend_pullback"


def test_no_fire_when_price_below_ema():
    rows = make_features_series(n=30, close_slope=-0.2, ema_offset=+1.0)
    assert detect_trend_pullback(rows) is None


def test_no_fire_when_insufficient_history():
    rows = make_features_series(n=5)
    assert detect_trend_pullback(rows) is None


def test_does_not_fire_when_regime_is_range():
    rows = make_features_series(
        n=35, close_slope=0.5, rsi_values=[45] * 30 + [35] * 3 + [42] * 2,
    )
    rows[-1] = rows[-1].model_copy(update={
        "close_price": rows[-2].close_price + Decimal("1.5"),
        "rsi_14": Decimal("42"),
        "ema_21": rows[-1].close_price - Decimal("0.5"),
        "trend_regime": "range",
        "vol_regime": "normal",
    })
    rows[-2] = rows[-2].model_copy(update={
        "rsi_14": Decimal("35"),
        "close_price": rows[-2].ema_21,
        "trend_regime": "range", "vol_regime": "normal",
    })
    rows[-3] = rows[-3].model_copy(update={
        "rsi_14": Decimal("38"),
        "trend_regime": "range", "vol_regime": "normal",
    })
    assert detect_trend_pullback(rows) is None


def test_does_not_fire_when_vol_regime_is_squeeze():
    rows = make_features_series(
        n=35, close_slope=0.5, rsi_values=[45] * 30 + [35] * 3 + [42] * 2,
    )
    rows[-1] = rows[-1].model_copy(update={
        "close_price": rows[-2].close_price + Decimal("1.5"),
        "rsi_14": Decimal("42"),
        "ema_21": rows[-1].close_price - Decimal("0.5"),
        "vol_regime": "squeeze",
    })
    rows[-2] = rows[-2].model_copy(update={
        "rsi_14": Decimal("35"),
        "close_price": rows[-2].ema_21,
        "vol_regime": "squeeze",
    })
    assert detect_trend_pullback(rows) is None
