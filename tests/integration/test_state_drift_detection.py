import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_drift_detected_when_state_says_2_db_says_0(env_for_postgres, tmp_path: Path):
    state_path = tmp_path / "STATE.md"
    state_path.write_text(
        "---\n"
        "shift_count: 1\n"
        "last_updated: 2026-04-26T14:00:00+00:00\n"
        "open_positions: 2\n"
        "open_theses: 0\n"
        "regime: choppy\n"
        "next_check_in_minutes: 60\n"
        "next_check_reason: x\n"
        "---\nbody\n"
    )
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        from trading_sandwich.triage.heartbeat import detect_state_drift

        result = asyncio.run(detect_state_drift(state_path))
        assert result["state_says"] == 2
        assert result["db_says"] == 0
        assert result["drift"] is True
