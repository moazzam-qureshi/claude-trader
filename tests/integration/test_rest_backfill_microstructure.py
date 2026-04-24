import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


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
def test_backfill_microstructure_inserts(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        base = datetime(2026, 4, 21, tzinfo=UTC)
        funding = [
            {"symbol": "BTCUSDT", "settlement_time": base + timedelta(hours=8 * i),
             "rate": Decimal("0.0001")}
            for i in range(30)
        ]
        oi_history = [
            {"symbol": "BTCUSDT", "captured_at": base + timedelta(hours=i),
             "open_interest_usd": Decimal("1000000000")}
            for i in range(7 * 24)
        ]

        with (
            patch(
                "trading_sandwich.ingestor.rest_backfill_microstructure._fetch_funding_window",
                new=AsyncMock(return_value=funding),
            ),
            patch(
                "trading_sandwich.ingestor.rest_backfill_microstructure._fetch_oi_history",
                new=AsyncMock(return_value=oi_history),
            ),
        ):
            from trading_sandwich.ingestor.rest_backfill_microstructure import (
                run_microstructure_backfill,
            )
            asyncio.run(run_microstructure_backfill(symbols=["BTCUSDT"]))

        assert _select_count(url, "raw_funding") == 30
        assert _select_count(url, "raw_open_interest") == 7 * 24
