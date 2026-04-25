import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_find_similar_signals_matches_by_structure(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.mcp.tools.reads import find_similar_signals

    async def _seed_and_call() -> None:
        factory = get_session_factory()
        seed_id = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=seed_id, symbol="BTCUSDT", timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
                candle_close_time=datetime(2026, 4, 25, tzinfo=timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                detector_version="test",
            ))
            for i, with_outcome in enumerate([True, False]):
                sid = uuid4()
                session.add(SignalORM(
                    signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                    archetype="trend_pullback",
                    fired_at=datetime(2026, 4, 24, tzinfo=timezone.utc) - timedelta(hours=i),
                    candle_close_time=datetime(2026, 4, 24, tzinfo=timezone.utc),
                    trigger_price=Decimal("67000"), direction="long",
                    confidence=Decimal("0.80"),
                    confidence_breakdown={},
                    gating_outcome="claude_triaged",
                    features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                    detector_version="test",
                ))
                if with_outcome:
                    session.add(SignalOutcome(
                        signal_id=sid, horizon="1h",
                        measured_at=datetime(2026, 4, 24, 1, tzinfo=timezone.utc),
                        close_price=Decimal("67500"),
                        return_pct=Decimal("0.008"), mfe_pct=Decimal("0.01"),
                        mae_pct=Decimal("0.002"),
                        stop_hit_1atr=False, target_hit_2atr=False,
                    ))
            session.add(SignalORM(
                signal_id=uuid4(), symbol="BTCUSDT", timeframe="5m",
                archetype="squeeze_breakout",
                fired_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
                candle_close_time=datetime(2026, 4, 24, tzinfo=timezone.utc),
                trigger_price=Decimal("67000"), direction="long",
                confidence=Decimal("0.80"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                detector_version="test",
            ))
            await session.commit()

        result = await find_similar_signals(seed_id, k=20)
        assert len(result.results) == 1
        assert result.sparse is True
        assert result.match_method == "structural"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call())
