import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_get_signal_loads_row_with_outcomes(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.mcp.tools.reads import get_signal

    async def _seed_and_call() -> None:
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid,
                symbol="BTCUSDT",
                timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
                candle_close_time=datetime(2026, 4, 25, tzinfo=timezone.utc),
                trigger_price=Decimal("68000"),
                direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={"x": 1},
                gating_outcome="claude_triaged",
                features_snapshot={"rsi_14": 55},
                detector_version="test",
            ))
            session.add(SignalOutcome(
                signal_id=sid,
                horizon="1h",
                measured_at=datetime(2026, 4, 25, 1, tzinfo=timezone.utc),
                close_price=Decimal("68500"),
                return_pct=Decimal("0.007"),
                mfe_pct=Decimal("0.01"),
                mae_pct=Decimal("0.002"),
                stop_hit_1atr=False,
                target_hit_2atr=False,
            ))
            await session.commit()
        detail = await get_signal(sid)
        assert detail.symbol == "BTCUSDT"
        assert len(detail.outcomes_so_far) == 1
        assert detail.outcomes_so_far[0]["horizon"] == "1h"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call())
