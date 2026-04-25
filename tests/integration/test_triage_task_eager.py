import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.mark.integration
def test_triage_signal_writes_claude_decisions_row(
    env_for_postgres, env_for_redis, monkeypatch
):
    from trading_sandwich.celery_app import app as celery_app
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {fake}")
    monkeypatch.setenv(
        "FAKE_CLAUDE_RESPONSE",
        json.dumps({
            "decision": "ignore",
            "rationale": "y" * 60,
            "alert_posted": False,
            "proposal_created": False,
        }),
    )

    async def _seed(_url: str):
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
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={},
                detector_version="test",
            ))
            await session.commit()
        return sid

    async def _check(sid):
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                select(ClaudeDecision).where(ClaudeDecision.signal_id == sid)
            )).scalar_one()
            # fake-claude doesn't actually call save_decision, so triage_signal
            # writes a fallback row with decision='ignore'
            assert row.decision == "ignore"
            assert row.rationale.startswith("(fallback)")

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        sid = asyncio.run(_seed(pg.get_connection_url()))

        from trading_sandwich.triage.worker import triage_signal
        triage_signal.delay(str(sid))

        asyncio.run(_check(sid))
