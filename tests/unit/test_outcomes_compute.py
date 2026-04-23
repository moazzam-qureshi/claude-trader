from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from trading_sandwich.outcomes.compute import measure_forward


def _candles_df(start: datetime, closes: list[float]) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "close_time": start + timedelta(minutes=i + 1),
            "open": c - 0.2, "high": c + 0.5, "low": c - 0.5, "close": c,
        })
    return pd.DataFrame(rows)


def test_measure_forward_long_winner():
    start = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    df = _candles_df(start, [101, 102, 103, 104, 105, 106])
    o = measure_forward(
        entry_price=Decimal("100"), direction="long", atr=Decimal("1.0"),
        candles=df,
    )
    assert o["close_price"] == Decimal("106")
    assert o["return_pct"] == Decimal("0.06")
    assert o["mfe_pct"] > Decimal("0.05")
    assert o["mae_pct"] <= Decimal("0")
    assert o["stop_hit_1atr"] is False
    assert o["target_hit_2atr"] is True


def test_measure_forward_long_stopped():
    start = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    df = _candles_df(start, [99, 98.5, 99, 100])
    o = measure_forward(
        entry_price=Decimal("100"), direction="long", atr=Decimal("1.0"),
        candles=df,
    )
    assert o["stop_hit_1atr"] is True
    assert o["time_to_stop_s"] is not None


def test_measure_forward_short():
    start = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    df = _candles_df(start, [99, 98, 97, 96, 95, 94])
    o = measure_forward(
        entry_price=Decimal("100"), direction="short", atr=Decimal("1.0"),
        candles=df,
    )
    assert o["return_pct"] == Decimal("0.06")
    assert o["target_hit_2atr"] is True
