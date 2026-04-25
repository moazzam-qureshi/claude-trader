from decimal import Decimal
from uuid import uuid4

import pytest

from trading_sandwich.contracts.phase2 import StopLossSpec


def _base_kwargs(**overrides):
    base = dict(
        decision_id=uuid4(),
        symbol="BTCUSDT", side="long", order_type="limit",
        size_usd=Decimal("500"), limit_price=Decimal("68000"),
        stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67000")),
        take_profit=None,
        opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
        alignment="a" * 40, similar_trades_evidence="b" * 80,
        expected_rr=Decimal("2.0"),
        worst_case_loss_usd=Decimal("7.35"),
        similar_signals_count=10,
        similar_signals_win_rate=Decimal("0.6"),
    )
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_propose_trade_rejects_worst_case_loss_mismatch():
    from trading_sandwich.mcp.tools.proposals import propose_trade
    with pytest.raises(ValueError, match="worst_case_loss_usd"):
        await propose_trade(**_base_kwargs(worst_case_loss_usd=Decimal("100")))


@pytest.mark.anyio
async def test_propose_trade_rejects_rr_below_minimum(monkeypatch):
    from trading_sandwich.mcp.tools.proposals import propose_trade
    monkeypatch.setattr(
        "trading_sandwich._policy.get_default_rr_minimum",
        lambda: Decimal("1.5"),
    )
    with pytest.raises(ValueError, match="expected_rr"):
        await propose_trade(**_base_kwargs(expected_rr=Decimal("1.0")))
