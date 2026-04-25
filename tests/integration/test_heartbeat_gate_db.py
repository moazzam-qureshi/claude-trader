import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift


@pytest.mark.integration
def test_query_returns_no_prior_when_table_empty(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.triage.heartbeat import _query_pacing_inputs

        inputs = asyncio.run(_query_pacing_inputs())
        assert inputs.last_spawned_at is None
        assert inputs.spawned_today == 0
        assert inputs.spawned_this_week == 0


@pytest.mark.integration
def test_record_skipped_shift_inserts_row(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.triage.heartbeat import record_skipped_shift

        async def _run():
            await record_skipped_shift(
                actual_interval_min=5,
                exit_reason="too_soon",
                prompt_version="abc",
            )
            factory = get_session_factory()
            async with factory() as session:
                rows = (await session.execute(select(HeartbeatShift))).scalars().all()
                return rows

        rows = asyncio.run(_run())
        skipped = [r for r in rows if r.spawned is False]
        assert len(skipped) == 1
        assert skipped[0].exit_reason == "too_soon"


@pytest.mark.integration
def test_query_counts_today_and_week(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.triage.heartbeat import _query_pacing_inputs

        async def _seed_and_query():
            factory = get_session_factory()
            now = datetime.now(timezone.utc)
            async with factory() as session:
                for delta_days in (0, 0, 0, 1, 6, 8):
                    session.add(HeartbeatShift(
                        started_at=now - timedelta(days=delta_days),
                        spawned=True,
                        next_check_in_minutes=60,
                        prompt_version="abc",
                    ))
                await session.commit()
            return await _query_pacing_inputs()

        inputs = asyncio.run(_seed_and_query())
        assert inputs.spawned_today == 3
        assert inputs.spawned_this_week == 5  # 0,0,0,1,6 = 5; 8d ago excluded
