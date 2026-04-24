from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.squeeze_breakout import detect_squeeze_breakout


def _apply_regime(rows, idx, trend, vol):
    rows[idx] = rows[idx].model_copy(update={"trend_regime": trend, "vol_regime": vol})


def test_fires_on_confirmed_upside_breakout():
    n = 60
    rows = make_features_series(n=n, close_slope=0.2, atr=1.0)
    # Bars 0..n-3 in squeeze; last two (n-2, n-1) expansion + breakout + confirm
    for i in range(n - 2):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })
    for i, close in ((n - 2, 102), (n - 1, 103)):
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
    n = 60
    rows = make_features_series(n=n, close_slope=0.0, atr=1.0)
    # All prior bars in squeeze, close inside band. Only last bar breaks out;
    # `prev` has close_price=99.5 (inside band) so no 2-bar confirmation.
    for i in range(n - 1):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "squeeze",
            "close_price": Decimal("99.5"),
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })
    rows[n - 1] = rows[n - 1].model_copy(update={
        "trend_regime": "range", "vol_regime": "expansion",
        "close_price": Decimal("102"),
        "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
    })
    assert detect_squeeze_breakout(rows) is None


def test_fires_on_downside_breakout():
    n = 60
    rows = make_features_series(n=n, close_slope=0.2, atr=1.0)
    for i in range(n - 2):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("101"), "bb_lower": Decimal("100"), "bb_middle": Decimal("100.5"),
        })
    for i, close in ((n - 2, 98), (n - 1, 97)):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "expansion",
            "close_price": Decimal(str(close)),
            "bb_upper": Decimal("101"), "bb_lower": Decimal("100"), "bb_middle": Decimal("100.5"),
        })
    s = detect_squeeze_breakout(rows)
    assert s is not None
    assert s.direction == "short"
