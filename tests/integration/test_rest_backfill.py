import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _select_count(async_url: str, where: str = "") -> int:
    async def _run() -> int:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text(
                    f"SELECT count(*) FROM raw_candles {where}"
                ))).scalar()
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_rest_backfill_inserts_candles(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Use current-month timestamps so rows land in an existing partition
        # (migration 0009 creates 13 partitions centred on deploy).
        from datetime import UTC, datetime
        now = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        base_ms = int(now.timestamp() * 1000)
        stub_once = [
            [base_ms,              "100", "105", "99",  "104", "10", base_ms + 299_999,
             "1040", 5, "5", "520", "0"],
            [base_ms + 300_000,    "104", "108", "103", "107", "12", base_ms + 599_999,
             "1284", 6, "7", "749", "0"],
            [base_ms + 600_000,    "107", "109", "105", "106", "8",  base_ms + 899_999,
             "856",  4, "3", "321", "0"],
        ]
        call_count = {"n": 0}

        async def stub_fetch(client, *, symbol, timeframe, start_ms, limit):
            call_count["n"] += 1
            # First call returns the 3 rows; subsequent calls return [] so the
            # pagination loop terminates.
            return stub_once if call_count["n"] == 1 else []

        with patch(
            "trading_sandwich.ingestor.rest_backfill._fetch_klines_page",
            new=AsyncMock(side_effect=stub_fetch),
        ):
            from trading_sandwich.ingestor.rest_backfill import run_backfill
            asyncio.run(run_backfill(
                symbols=["BTCUSDT"], timeframes=["5m"], days=1,
            ))

        assert _select_count(url) == 3
        assert _select_count(url, where="WHERE symbol='BTCUSDT' AND timeframe='5m'") == 3
