import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from trading_sandwich.contracts.phase2 import AccountState


@pytest.mark.integration
def test_live_mode_blocks_without_api_key(env_for_postgres, monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution import policy_rails
    from trading_sandwich.execution.policy_rails import evaluate_policy

    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
    monkeypatch.setattr(_policy, "get_execution_mode", lambda: "live")
    monkeypatch.setattr(_policy, "get_max_order_usd", lambda: Decimal("500"))
    monkeypatch.setattr(_policy, "get_universe_symbols", lambda: ["BTCUSDT"])
    monkeypatch.setattr(
        _policy, "get_first_trade_size_multiplier", lambda: Decimal("1.0"),
    )

    class _FakeSettings:
        binance_api_key = ""

    monkeypatch.setattr(policy_rails, "get_settings", lambda: _FakeSettings())

    async def _flow():
        proposal = SimpleNamespace(
            proposal_id=uuid4(), symbol="BTCUSDT", side="long",
            order_type="market", size_usd=Decimal("100"), limit_price=None,
            stop_loss={"kind": "fixed_price", "value": "67000"},
            take_profit=None,
        )
        good_state = AccountState(
            equity_usd=Decimal("10000"), free_margin_usd=Decimal("8000"),
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_today_usd=Decimal("0"),
            open_positions_count=0, leverage_used=Decimal("0"),
        )
        with patch(
            "trading_sandwich.execution.policy_rails._account_state",
            AsyncMock(return_value=good_state),
        ):
            block = await evaluate_policy(proposal)
        assert block is not None
        assert "live_mode" in block

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
