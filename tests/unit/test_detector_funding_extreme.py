from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.funding_extreme import detect_funding_extreme


def test_long_when_funding_below_threshold():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "BTCUSDT",
        "funding_rate": Decimal("-0.0010"),  # below the -0.0003 BTC threshold
        "vol_regime": "normal",
    })
    s = detect_funding_extreme(rows)
    assert s is not None
    assert s.direction == "long"


def test_short_when_funding_above_threshold():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "BTCUSDT",
        "funding_rate": Decimal("0.0010"),   # above 0.0003 threshold
        "vol_regime": "normal",
    })
    s = detect_funding_extreme(rows)
    assert s is not None
    assert s.direction == "short"


def test_uses_default_threshold_for_unknown_symbol():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "NEWCOIN",
        "funding_rate": Decimal("-0.0010"),  # below -0.0005 default
        "vol_regime": "normal",
    })
    s = detect_funding_extreme(rows)
    assert s is not None
    assert s.direction == "long"


def test_no_fire_when_vol_is_squeeze():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "BTCUSDT",
        "funding_rate": Decimal("0.0010"),
        "vol_regime": "squeeze",
    })
    assert detect_funding_extreme(rows) is None
