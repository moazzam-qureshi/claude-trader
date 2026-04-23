import os
import subprocess

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_doctor_exits_zero_when_db_reachable(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        env = os.environ.copy()
        env["PYTHONPATH"] = "/app/src"
        result = subprocess.run(
            ["python", "-m", "trading_sandwich.cli", "doctor"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "database" in result.stdout.lower()


@pytest.mark.integration
def test_stats_prints_each_table(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        env = os.environ.copy()
        env["PYTHONPATH"] = "/app/src"
        result = subprocess.run(
            ["python", "-m", "trading_sandwich.cli", "stats"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for tbl in ["raw_candles", "features", "signals", "signal_outcomes", "claude_decisions"]:
            assert f"{tbl}: 0" in result.stdout, f"{tbl} row count missing:\n{result.stdout}"
