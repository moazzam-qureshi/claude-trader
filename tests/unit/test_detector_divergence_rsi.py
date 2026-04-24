from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.divergence_rsi import detect_divergence_rsi


def test_fires_on_bullish_divergence():
    rows = make_features_series(n=40, close_slope=0.0, atr=1.0)
    # Engineer two price lows at indices 3 and 38: later price LOWER, RSI HIGHER.
    # All rows in trend_down + normal regime (counter-trend long).
    for i in range(40):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "trend_down", "vol_regime": "normal",
        })
    # First price low at i=3: price 95, RSI 25
    rows[3] = rows[3].model_copy(update={
        "close_price": Decimal("95"), "rsi_14": Decimal("25"),
    })
    # Second (more recent) price low at i=38: price 94, RSI 35
    rows[38] = rows[38].model_copy(update={
        "close_price": Decimal("94"), "rsi_14": Decimal("35"),
    })
    # Keep all other closes above 94 so the pivot-pair picked is (3, 38)
    for i in range(40):
        if i in (3, 38):
            continue
        rows[i] = rows[i].model_copy(update={
            "close_price": Decimal("100"), "rsi_14": Decimal("50"),
        })

    s = detect_divergence_rsi(rows)
    assert s is not None
    assert s.direction == "long"
    assert s.archetype == "divergence_rsi"


def test_does_not_fire_in_squeeze():
    rows = make_features_series(n=40, close_slope=-0.3, atr=1.0)
    for i in range(40):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "squeeze",
        })
    assert detect_divergence_rsi(rows) is None
