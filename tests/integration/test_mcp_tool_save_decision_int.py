import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_save_decision_upsert_on_signal_invocation_mode(env_for_postgres):
    from trading_sandwich.contracts.phase2 import AlertPayload
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.mcp.tools.decisions import save_decision

    async def _flow() -> None:
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={}, gating_outcome="claude_triaged",
                features_snapshot={}, detector_version="test",
            ))
            await session.commit()

        await save_decision(
            signal_id=sid, decision="alert", rationale="x" * 60,
            alert_payload=AlertPayload(
                title="t", body="b", signal_id=sid, decision_id=uuid4()
            ),
        )
        await save_decision(
            signal_id=sid, decision="ignore", rationale="y" * 60,
        )

        async with factory() as session:
            rows = (await session.execute(
                select(ClaudeDecision).where(ClaudeDecision.signal_id == sid)
            )).scalars().all()
            assert len(rows) == 1
            assert rows[0].decision == "ignore"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
