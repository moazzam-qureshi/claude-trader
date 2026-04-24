"""Test helpers: fabricate features rows for detector unit tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_sandwich.contracts.models import FeaturesRow


def make_features_series(
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    n: int = 40,
    *,
    close_start: float = 100.0,
    close_slope: float = 0.5,
    rsi_values: list[float] | None = None,
    ema_offset: float = -1.0,
    atr: float = 1.0,
    start: datetime | None = None,
) -> list[FeaturesRow]:
    start = start or datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    rows: list[FeaturesRow] = []
    for i in range(n):
        close = close_start + i * close_slope
        rsi = rsi_values[i] if rsi_values and i < len(rsi_values) else 50.0
        rows.append(FeaturesRow(
            symbol=symbol, timeframe=timeframe,
            close_time=start + timedelta(minutes=i),
            close_price=Decimal(str(round(close, 4))),
            ema_21=Decimal(str(round(close + ema_offset, 4))),
            rsi_14=Decimal(str(round(rsi, 2))),
            atr_14=Decimal(str(round(atr, 4))),
            trend_regime="trend_up",
            vol_regime="normal",
            feature_version="test",
        ))
    return rows
