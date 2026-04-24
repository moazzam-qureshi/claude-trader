import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_scan_gaps_identifies_missing_opens(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime.now(UTC).replace(second=0, microsecond=0)
        # Align start to a 5m boundary so _scan_gaps_async's expected_candle_opens
        # produces opens that match what we seed.
        now = now.replace(minute=(now.minute // 5) * 5)
        start = now - timedelta(minutes=30)

        async def _seed():
            engine = create_async_engine(url)
            try:
                async with engine.begin() as conn:
                    for i in range(6):
                        ot = start + timedelta(minutes=5 * i)
                        if i == 3:
                            continue  # leave gap
                        ct = ot + timedelta(minutes=5)
                        await conn.execute(text(
                            "INSERT INTO raw_candles (symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                            "VALUES (:s,:tf,:ot,:ct,100,101,99,100,10)"
                        ), {"s": "BTCUSDT", "tf": "5m", "ot": ot, "ct": ct})
            finally:
                await engine.dispose()
        asyncio.run(_seed())

        from trading_sandwich.ingestor.backfill import _scan_gaps_async
        missing = asyncio.run(_scan_gaps_async("BTCUSDT", "5m", lookback_hours=1))
        assert len(missing) >= 1
