from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.liquidity_sweep_daily import detect_liquidity_sweep_daily


def test_fires_on_prior_day_high_sweep_short():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "prior_day_high": Decimal("110"),
        "prior_day_low":  Decimal("100"),
        "swing_high_5":   Decimal("110.5"),
        "swing_low_5":    Decimal("100.5"),
        "close_price":    Decimal("109"),
    })
    s = detect_liquidity_sweep_daily(rows)
    assert s is not None
    assert s.direction == "short"


def test_fires_on_prior_day_low_sweep_long():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "prior_day_high": Decimal("110"),
        "prior_day_low":  Decimal("100"),
        "swing_high_5":   Decimal("109"),
        "swing_low_5":    Decimal("99.5"),
        "close_price":    Decimal("101"),
    })
    s = detect_liquidity_sweep_daily(rows)
    assert s is not None
    assert s.direction == "long"


def test_no_fire_when_close_remains_beyond():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "prior_day_high": Decimal("110"),
        "prior_day_low":  Decimal("100"),
        "swing_high_5":   Decimal("110.5"),
        "swing_low_5":    Decimal("100.5"),
        "close_price":    Decimal("111"),
    })
    assert detect_liquidity_sweep_daily(rows) is None
