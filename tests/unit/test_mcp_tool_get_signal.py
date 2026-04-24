import datetime as _dt
import decimal as _dec
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_get_signal_returns_signal_detail_shape():
    from trading_sandwich.mcp.tools.reads import get_signal

    signal_id = uuid4()
    fake_row = {
        "signal_id": signal_id,
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "archetype": "trend_pullback",
        "direction": "long",
        "fired_at": _dt.datetime(2026, 4, 25, tzinfo=_dt.timezone.utc),
        "trigger_price": _dec.Decimal("68000"),
        "confidence": _dec.Decimal("0.85"),
        "confidence_breakdown": {"rule_strength": 0.9},
        "features_snapshot": {"rsi_14": 55},
    }
    with patch("trading_sandwich.mcp.tools.reads._load_signal_with_outcomes",
               AsyncMock(return_value=(fake_row, []))):
        result = await get_signal(signal_id)
    assert result.signal_id == signal_id
    assert result.symbol == "BTCUSDT"
    assert result.outcomes_so_far == []


@pytest.mark.anyio
async def test_get_signal_raises_on_missing():
    from trading_sandwich.mcp.tools.reads import get_signal

    with patch(
        "trading_sandwich.mcp.tools.reads._load_signal_with_outcomes",
        AsyncMock(return_value=(None, [])),
    ):
        with pytest.raises(ValueError, match="signal .* not found"):
            await get_signal(uuid4())
