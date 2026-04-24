from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.squeeze_breakout import detect_squeeze_breakout


def _apply_regime(rows, idx, trend, vol):
    rows[idx] = rows[idx].model_copy(update={"trend_regime": trend, "vol_regime": vol})


def test_fires_on_confirmed_upside_breakout():
    rows = make_features_series(n=30, close_slope=0.2, atr=1.0)
    for i in range(28):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })
    for i, close in ((28, 102), (29, 103)):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "expansion",
            "close_price": Decimal(str(close)),
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })

    s = detect_squeeze_breakout(rows)
    assert s is not None
    assert s.direction == "long"
    assert s.archetype == "squeeze_breakout"


def test_does_not_fire_without_confirmation_bar():
    rows = make_features_series(n=30, close_slope=0.2, atr=1.0)
    for i in range(29):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })
    rows[29] = rows[29].model_copy(update={
        "trend_regime": "range", "vol_regime": "expansion",
        "close_price": Decimal("102"),
        "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
    })
    assert detect_squeeze_breakout(rows) is None


def test_fires_on_downside_breakout():
    rows = make_features_series(n=30, close_slope=0.2, atr=1.0)
    for i in range(28):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("101"), "bb_lower": Decimal("100"), "bb_middle": Decimal("100.5"),
        })
    for i, close in ((28, 98), (29, 97)):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "expansion",
            "close_price": Decimal(str(close)),
            "bb_upper": Decimal("101"), "bb_lower": Decimal("100"), "bb_middle": Decimal("100.5"),
        })
    s = detect_squeeze_breakout(rows)
    assert s is not None
    assert s.direction == "short"
