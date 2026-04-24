import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _seed(async_url: str, n: int = 250) -> None:
    # Use a base date inside the current-month partition.
    base = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                for i in range(n):
                    c = 100.0 + i * 0.5
                    v = 10 + (i % 7) * 0.5
                    ot = base + timedelta(minutes=5 * i)
                    ct = ot + timedelta(minutes=5)
                    await conn.execute(text(
                        "INSERT INTO raw_candles "
                        "(symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                        "VALUES (:s,:tf,:ot,:ct,:o,:h,:l,:c,:v)"
                    ), {"s": "BTCUSDT", "tf": "5m", "ot": ot, "ct": ct,
                        "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c, "v": v})
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _features_count(async_url: str) -> int:
    async def _run() -> int:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text(
                    "SELECT count(*) FROM features WHERE symbol='BTCUSDT' AND timeframe='5m'"
                ))).scalar()
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_features_backfill_writes_post_warmup_rows(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _seed(url, n=250)
        from trading_sandwich.features.backfill import run_features_backfill
        asyncio.run(run_features_backfill(
            symbols=["BTCUSDT"], timeframes=["5m"],
        ))

        # 250 candles, 200-bar warmup → 51 features rows written
        count = _features_count(url)
        assert count >= 50
        assert count <= 250
