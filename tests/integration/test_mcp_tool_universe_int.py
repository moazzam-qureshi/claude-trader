import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Position
from trading_sandwich.mcp.tools.universe import get_open_positions


@pytest.mark.integration
def test_get_open_positions_empty(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _run():
            return await get_open_positions()

        result = asyncio.run(_run())
        assert result == []


@pytest.mark.integration
def test_get_open_positions_returns_open_only(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _seed_and_query():
            factory = get_session_factory()
            async with factory() as session:
                session.add(Position(
                    symbol="BTCUSDT",
                    opened_at=datetime(2026, 4, 26, 10, 0, tzinfo=timezone.utc),
                    side="long",
                    size_base=Decimal("0.5"),
                    avg_entry=Decimal("70000"),
                    closed_at=None,
                ))
                session.add(Position(
                    symbol="ETHUSDT",
                    opened_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
                    side="long",
                    size_base=Decimal("2"),
                    avg_entry=Decimal("3500"),
                    closed_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
                ))
                await session.commit()
            return await get_open_positions()

        result = asyncio.run(_seed_and_query())
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"
        assert result[0]["side"] == "long"
