from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_sandwich.contracts.phase2 import OrderRequest, StopLossSpec


@pytest.mark.anyio
async def test_paper_market_order_fills_at_last_close():
    from trading_sandwich.execution.adapters.paper import PaperAdapter

    adapter = PaperAdapter()
    request = OrderRequest(
        symbol="BTCUSDT", side="long", order_type="market",
        size_usd=Decimal("500"),
        stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67000")),
        client_order_id="paper-1",
    )
    with patch(
        "trading_sandwich.execution.adapters.paper._latest_close_price",
        AsyncMock(return_value=Decimal("68000")),
    ):
        receipt = await adapter.submit_order(request)
    assert receipt.status == "filled"
    assert receipt.avg_fill_price == Decimal("68000")
    assert receipt.exchange_order_id is not None


@pytest.mark.anyio
async def test_paper_limit_order_marked_open():
    from trading_sandwich.execution.adapters.paper import PaperAdapter

    adapter = PaperAdapter()
    request = OrderRequest(
        symbol="BTCUSDT", side="long", order_type="limit",
        size_usd=Decimal("500"), limit_price=Decimal("67500"),
        stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67000")),
        client_order_id="paper-2",
    )
    with patch(
        "trading_sandwich.execution.adapters.paper._latest_close_price",
        AsyncMock(return_value=Decimal("68000")),
    ):
        receipt = await adapter.submit_order(request)
    assert receipt.status == "open"
    assert receipt.avg_fill_price is None


@pytest.mark.anyio
async def test_paper_account_state_starts_at_seed_equity(monkeypatch):
    from trading_sandwich.execution.adapters.paper import PaperAdapter

    monkeypatch.setattr(
        "trading_sandwich._policy.get_paper_starting_equity_usd",
        lambda: Decimal("10000"),
    )
    adapter = PaperAdapter()
    state = await adapter.get_account_state()
    assert state.equity_usd == Decimal("10000")
    assert state.realized_pnl_today_usd == Decimal("0")
