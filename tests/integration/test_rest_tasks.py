import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


def _select_count(async_url: str, table: str) -> int:
    async def _run() -> int:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_poll_funding_writes_rows(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)
        command.upgrade(Config("alembic.ini"), "head")

        from datetime import UTC, datetime
        stub_rows = [
            {"symbol": "BTCUSDT",
             "settlement_time": datetime(2026, 4, 21, 0, tzinfo=UTC),
             "rate": Decimal("0.0001")},
            {"symbol": "BTCUSDT",
             "settlement_time": datetime(2026, 4, 21, 8, tzinfo=UTC),
             "rate": Decimal("0.00015")},
        ]
        with patch(
            "trading_sandwich.ingestor.rest_tasks.fetch_funding_rate",
            new=AsyncMock(return_value=stub_rows),
        ):
            from trading_sandwich.ingestor.rest_tasks import poll_funding
            poll_funding.run("BTCUSDT")

        assert _select_count(pg_url, "raw_funding") == 2


@pytest.mark.integration
def test_poll_open_interest_writes_row(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)
        command.upgrade(Config("alembic.ini"), "head")

        from datetime import UTC, datetime
        stub_row = {
            "symbol": "BTCUSDT",
            "captured_at": datetime(2026, 4, 21, 12, tzinfo=UTC),
            "open_interest_usd": Decimal("12345678900"),
        }
        with (
            patch(
                "trading_sandwich.ingestor.rest_tasks.fetch_open_interest",
                new=AsyncMock(return_value=stub_row),
            ),
            patch(
                "trading_sandwich.ingestor.rest_tasks._latest_mark_price",
                new=AsyncMock(return_value=Decimal("100000")),
            ),
        ):
            from trading_sandwich.ingestor.rest_tasks import poll_open_interest
            poll_open_interest.run("BTCUSDT")

        assert _select_count(pg_url, "raw_open_interest") == 1
