import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_get_market_snapshot_rolls_up_per_timeframe(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Features
    from trading_sandwich.mcp.tools.reads import get_market_snapshot

    async def _seed_and_call() -> None:
        factory = get_session_factory()
        async with factory() as session:
            for tf in ("5m", "1h"):
                session.add(Features(
                    symbol="BTCUSDT", timeframe=tf,
                    close_time=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
                    close_price=Decimal("68000"),
                    trend_regime="trend_up", vol_regime="normal",
                    ema_21=Decimal("67500"),
                    atr_14=Decimal("500"),
                    feature_version="test",
                ))
            await session.commit()
        snap = await get_market_snapshot("BTCUSDT")
        # both timeframes seeded; 15m, 4h, 1d return None and should be in dict
        assert snap.per_timeframe["5m"]["trend_regime"] == "trend_up"
        assert snap.per_timeframe["1h"]["trend_regime"] == "trend_up"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call())
