import asyncio
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift


@pytest.mark.integration
def test_first_tick_spawns_and_records(env_for_postgres, monkeypatch, tmp_path):
    """First tick on empty DB → spawn (mocked) and record one row."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "STATE.md").write_text(
        "---\n"
        "shift_count: 0\n"
        "last_updated: 2026-04-26T00:00:00+00:00\n"
        "open_positions: 0\n"
        "open_theses: 0\n"
        "regime: bootstrap\n"
        "next_check_in_minutes: 60\n"
        "next_check_reason: bootstrap\n"
        "---\nbody\n"
    )
    monkeypatch.setattr(
        "trading_sandwich.triage.heartbeat.RUNTIME_DIR", runtime
    )

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.triage import heartbeat as hb
        from trading_sandwich.triage.shift_invocation import ShiftRunResult

        async def _fake_spawn(*, argv, cwd, timeout_seconds):
            return ShiftRunResult(
                returncode=0, stdout="ok", stderr="",
                duration_seconds=10,
            )

        monkeypatch.setattr(hb, "_spawn_claude_shift", _fake_spawn)

        asyncio.run(hb.heartbeat_tick())

        async def _query():
            factory = get_session_factory()
            async with factory() as session:
                return (await session.execute(
                    select(HeartbeatShift)
                )).scalars().all()

        rows = asyncio.run(_query())
        assert len(rows) == 1
        assert rows[0].spawned is True
        assert rows[0].exit_reason == "completed"


@pytest.mark.integration
def test_immediate_second_tick_skips(env_for_postgres, monkeypatch):
    """A second tick right after a spawned one → exit_reason='too_soon'."""
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.triage import heartbeat as hb

        async def _seed_recent_spawn():
            factory = get_session_factory()
            async with factory() as session:
                session.add(HeartbeatShift(
                    started_at=datetime.now(timezone.utc),
                    spawned=True,
                    next_check_in_minutes=60,
                    prompt_version="abc",
                ))
                await session.commit()

        asyncio.run(_seed_recent_spawn())

        async def _spawn_must_not_be_called(**kw):
            raise AssertionError("should not spawn")

        monkeypatch.setattr(hb, "_spawn_claude_shift", _spawn_must_not_be_called)

        asyncio.run(hb.heartbeat_tick())

        async def _query():
            factory = get_session_factory()
            async with factory() as session:
                return (await session.execute(
                    select(HeartbeatShift).where(HeartbeatShift.spawned.is_(False))
                )).scalars().all()

        rows = asyncio.run(_query())
        assert len(rows) == 1
        assert rows[0].exit_reason == "too_soon"
