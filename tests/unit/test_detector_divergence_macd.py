from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.divergence_macd import detect_divergence_macd


def test_fires_on_bearish_macd_divergence_in_uptrend():
    rows = make_features_series(n=40, close_slope=0.0, atr=1.0)
    for i in range(40):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "trend_up", "vol_regime": "normal",
            "close_price": Decimal("100"), "macd_hist": Decimal("0.3"),
        })
    # First high at i=3: price 107, macd hist 1.5
    rows[3] = rows[3].model_copy(update={
        "close_price": Decimal("107"), "macd_hist": Decimal("1.5"),
    })
    # Later high at i=38: price HIGHER (110), macd hist LOWER (0.5) → bearish div
    rows[38] = rows[38].model_copy(update={
        "close_price": Decimal("110"), "macd_hist": Decimal("0.5"),
    })

    s = detect_divergence_macd(rows)
    assert s is not None
    assert s.direction == "short"
    assert s.archetype == "divergence_macd"
