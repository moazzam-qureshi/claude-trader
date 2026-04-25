import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_archetype_stats_groups_by_regime_and_direction(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.mcp.tools.reads import get_archetype_stats

    async def _seed_and_call() -> None:
        factory = get_session_factory()
        async with factory() as session:
            for n, ret in enumerate([0.02, -0.01, 0.005]):
                sid = uuid4()
                session.add(SignalORM(
                    signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                    archetype="trend_pullback",
                    fired_at=datetime.now(timezone.utc) - timedelta(days=n),
                    candle_close_time=datetime.now(timezone.utc) - timedelta(days=n),
                    trigger_price=Decimal("68000"), direction="long",
                    confidence=Decimal("0.80"),
                    confidence_breakdown={},
                    gating_outcome="claude_triaged",
                    features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                    detector_version="test",
                ))
                session.add(SignalOutcome(
                    signal_id=sid, horizon="24h",
                    measured_at=datetime.now(timezone.utc),
                    close_price=Decimal("68000"),
                    return_pct=Decimal(str(ret)), mfe_pct=Decimal("0.025"),
                    mae_pct=Decimal("-0.015"),
                    stop_hit_1atr=False, target_hit_2atr=False,
                ))
            await session.commit()
        stats = await get_archetype_stats("trend_pullback", lookback_days=30)
        assert stats.total_fires == 3
        assert any(b["count"] == 3 for b in stats.by_bucket)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call())
