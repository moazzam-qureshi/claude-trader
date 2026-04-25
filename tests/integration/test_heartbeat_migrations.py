import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _check_table_exists(async_url: str, table: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(f"SELECT to_regclass('public.{table}')"))
                assert r.scalar() == table, f"{table} missing"
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _check_columns(async_url: str, table: str, required: list[str]) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t"
                ), {"t": table})
                cols = {row[0] for row in r}
                for col in required:
                    assert col in cols, f"missing column {col} in {table}"
        finally:
            await engine.dispose()
    asyncio.run(_run())


_HEARTBEAT_SHIFTS_COLS = [
    "id", "started_at", "ended_at",
    "requested_interval_min", "actual_interval_min", "interval_clamped",
    "spawned", "exit_reason",
    "claude_session_id", "duration_seconds", "tools_called",
    "next_check_in_minutes", "next_check_reason",
    "input_tokens", "output_tokens",
    "diary_file", "state_snapshot", "prompt_version",
]


@pytest.mark.integration
def test_heartbeat_shifts_table_exists(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_table_exists(url, "heartbeat_shifts")


@pytest.mark.integration
def test_heartbeat_shifts_has_required_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _check_columns(url, "heartbeat_shifts", _HEARTBEAT_SHIFTS_COLS)
