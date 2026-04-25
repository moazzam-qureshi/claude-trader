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
def test_evaluate_policy_trips_kill_switch_on_loss_breach(env_for_postgres, monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.kill_switch import is_active

    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)

    async def _flow():
        bad_state = AccountState(
            equity_usd=Decimal("10000"),
            free_margin_usd=Decimal("8000"),
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_today_usd=Decimal("-300"),
            open_positions_count=0,
            leverage_used=Decimal("0"),
        )
        proposal = SimpleNamespace(
            proposal_id=uuid4(),
            symbol="BTCUSDT", side="long", order_type="market",
            size_usd=Decimal("100"), limit_price=None,
            stop_loss={"kind": "fixed_price", "value": "67000"},
            take_profit=None,
        )

        with patch(
            "trading_sandwich.execution.policy_rails._account_state",
            AsyncMock(return_value=bad_state),
        ):
            from trading_sandwich.execution.policy_rails import evaluate_policy
            block = await evaluate_policy(proposal)
            assert block is not None
            assert "max_daily_realized_loss" in block
        assert await is_active() is True

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
