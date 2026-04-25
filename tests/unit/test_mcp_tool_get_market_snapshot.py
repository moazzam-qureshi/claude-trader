from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_get_market_snapshot_returns_per_timeframe_dict():
    from trading_sandwich.mcp.tools.reads import get_market_snapshot

    def _row(_tf: str) -> dict:
        return {
            "close_price": float(Decimal("68000")),
            "trend_regime": "trend_up",
            "vol_regime": "normal",
            "ema_8": float(Decimal("67900")),
            "ema_21": float(Decimal("67500")),
            "ema_55": float(Decimal("67000")),
            "ema_200": float(Decimal("65000")),
            "adx_14": float(Decimal("22")),
            "atr_percentile_100": float(Decimal("0.35")),
            "bb_width_percentile_100": float(Decimal("0.5")),
            "funding_rate": float(Decimal("0.0001")),
            "open_interest_usd": float(Decimal("100000000")),
            "prior_day_high": float(Decimal("68500")),
            "prior_day_low": float(Decimal("67200")),
            "prior_week_high": float(Decimal("69000")),
            "prior_week_low": float(Decimal("66500")),
            "pivot_p": float(Decimal("67850")),
            "atr_14": float(Decimal("500")),
        }

    rows = {tf: _row(tf) for tf in ("5m", "15m", "1h", "4h", "1d")}
    with patch(
        "trading_sandwich.mcp.tools.reads._load_latest_features",
        AsyncMock(side_effect=lambda sym, tf: rows[tf]),
    ), patch(
        "trading_sandwich.mcp.tools.reads._policy_timeframes",
        return_value=list(rows.keys()),
    ):
        snap = await get_market_snapshot("BTCUSDT")
    assert snap.symbol == "BTCUSDT"
    assert set(snap.per_timeframe.keys()) == {"5m", "15m", "1h", "4h", "1d"}
    assert snap.per_timeframe["1h"]["trend_regime"] == "trend_up"


@pytest.mark.anyio
async def test_get_market_snapshot_tolerates_missing_timeframe():
    from trading_sandwich.mcp.tools.reads import get_market_snapshot

    with patch(
        "trading_sandwich.mcp.tools.reads._load_latest_features",
        AsyncMock(return_value=None),
    ), patch(
        "trading_sandwich.mcp.tools.reads._policy_timeframes",
        return_value=["5m"],
    ):
        snap = await get_market_snapshot("BTCUSDT")
    assert snap.per_timeframe["5m"] is None
