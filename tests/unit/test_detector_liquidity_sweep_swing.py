from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.liquidity_sweep_swing import detect_liquidity_sweep_swing


def test_fires_when_wick_beyond_swing_high_and_closes_back():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    # Prior 20 bars establish swing-high ~110 and swing-low ~98 via individual bar H/L proxies.
    for i, sh in enumerate([108, 108, 108, 109, 110, 109, 108, 108, 108, 108,
                            108, 108, 108, 108, 108, 108, 108, 108, 108, 108]):
        rows[-21 + i] = rows[-21 + i].model_copy(update={
            "swing_high_5": Decimal(str(sh)),
            "swing_low_5":  Decimal("98"),
        })
    # Current bar wicked above 110 and closed back inside.
    rows[-1] = rows[-1].model_copy(update={
        "swing_high_5": Decimal("111"),
        "swing_low_5":  Decimal("105"),
        "close_price":  Decimal("108"),
    })
    s = detect_liquidity_sweep_swing(rows)
    assert s is not None
    assert s.direction == "short"
