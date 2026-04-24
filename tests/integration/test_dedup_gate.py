import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _insert_signal(async_url: str, *, symbol, timeframe, direction, gating_outcome, fired_at):
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO signals (signal_id,symbol,timeframe,archetype,"
                    "fired_at,candle_close_time,trigger_price,direction,confidence,"
                    "confidence_breakdown,gating_outcome,features_snapshot,detector_version) "
                    "VALUES (:id,:s,:tf,:a,:f,:f,100,:d,0.8,CAST('{}' AS jsonb),:go,"
                    "CAST('{}' AS jsonb),'test')"
                ), {"id": uuid4(), "s": symbol, "tf": timeframe,
                    "a": "trend_pullback", "f": fired_at,
                    "d": direction, "go": gating_outcome})
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_dedup_suppresses_5m_when_higher_tf_recent(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        _insert_signal(url, symbol="BTCUSDT", timeframe="1h", direction="long",
                       gating_outcome="claude_triaged", fired_at=now - timedelta(minutes=10))

        from trading_sandwich.signals.dedup import is_dedup_suppressed
        suppressed = is_dedup_suppressed(
            symbol="BTCUSDT", direction="long", timeframe="5m",
            fired_at=now, window_minutes=30,
        )
        assert suppressed is True


@pytest.mark.integration
def test_dedup_does_not_suppress_same_tf(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        _insert_signal(url, symbol="BTCUSDT", timeframe="5m", direction="long",
                       gating_outcome="claude_triaged", fired_at=now - timedelta(minutes=10))

        from trading_sandwich.signals.dedup import is_dedup_suppressed
        suppressed = is_dedup_suppressed(
            symbol="BTCUSDT", direction="long", timeframe="5m",
            fired_at=now, window_minutes=30,
        )
        assert suppressed is False


@pytest.mark.integration
def test_dedup_does_not_suppress_opposite_direction(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        _insert_signal(url, symbol="BTCUSDT", timeframe="1h", direction="short",
                       gating_outcome="claude_triaged", fired_at=now - timedelta(minutes=10))

        from trading_sandwich.signals.dedup import is_dedup_suppressed
        assert not is_dedup_suppressed(
            symbol="BTCUSDT", direction="long", timeframe="5m",
            fired_at=now, window_minutes=30,
        )
