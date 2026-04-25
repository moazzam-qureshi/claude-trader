import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM


@pytest.mark.integration
def test_get_recent_signals_returns_recent_only(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.mcp.tools.market_scan import get_recent_signals

        async def _seed_and_query():
            factory = get_session_factory()
            now = datetime.now(timezone.utc)
            async with factory() as session:
                for hours_ago in (1, 5, 50):
                    s = SignalORM(
                        signal_id=uuid4(),
                        symbol="BTCUSDT",
                        timeframe="5m",
                        archetype="range_rejection",
                        direction="long",
                        fired_at=now - timedelta(hours=hours_ago),
                        candle_close_time=now - timedelta(hours=hours_ago),
                        trigger_price=Decimal("70000"),
                        confidence=Decimal("0.5"),
                        confidence_breakdown={},
                        gating_outcome="claude_triaged",
                        features_snapshot={},
                        detector_version="test",
                    )
                    session.add(s)
                await session.commit()
            return await get_recent_signals(symbol="BTCUSDT", since="6h")

        result = asyncio.run(_seed_and_query())
        assert len(result) == 2


@pytest.mark.integration
def test_get_top_movers_returns_list(monkeypatch):
    from trading_sandwich.mcp.tools import market_scan

    async def _fake_fetch(window, limit):
        return [
            {"symbol": "SUIUSDT", "change_pct": 18.0, "volume_usd": 340_000_000},
            {"symbol": "ARBUSDT", "change_pct": 12.0, "volume_usd": 200_000_000},
        ]

    monkeypatch.setattr(market_scan, "_fetch_top_movers", _fake_fetch)
    result = asyncio.run(market_scan.get_top_movers(window="24h", limit=10))
    assert len(result) == 2
    assert result[0]["symbol"] == "SUIUSDT"
