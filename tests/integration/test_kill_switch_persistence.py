import asyncio

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_kill_switch_round_trip(env_for_postgres):
    from trading_sandwich.execution.kill_switch import is_active, resume, trip

    async def _flow():
        assert await is_active() is False
        await trip(reason="max_daily_realized_loss_breached")
        assert await is_active() is True
        await resume(ack_reason="manual review complete")
        assert await is_active() is False

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
