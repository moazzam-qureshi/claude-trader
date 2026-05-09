"""Integration tests for `cli settings` subcommands.

  cli settings bootstrap                    # idempotent first-boot seed
  cli settings reseed --key K               # restore K to YAML default
  cli settings list [--prefix P]            # current values
  cli settings get K                        # one value + tier marker

These wrap settings.seed.bootstrap() and the same DB read paths used
by the MCP/Discord surfaces. CLI is the operator's terminal-side
equivalent of /settings list/get and is also what `cli doctor`
recommends running on first deploy.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md \xc2\xa712.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _query(async_url: str, sql: str, params: dict | None = None) -> list[tuple]:
    async def _run():
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(sql), params or {})
                return [tuple(row) for row in r]
        finally:
            await engine.dispose()
    return asyncio.run(_run())


def _run_cli(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = "/app/src"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["python", "-m", "trading_sandwich.cli", *args],
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.mark.integration
def test_cli_settings_bootstrap_seeds_db(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = _run_cli("settings", "bootstrap")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "inserted" in result.stdout.lower()

        n = _query(url, "SELECT COUNT(*) FROM policy_settings")[0][0]
        assert n > 10


@pytest.mark.integration
def test_cli_settings_bootstrap_idempotent(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        first = _run_cli("settings", "bootstrap")
        n_after_first = _query(url, "SELECT COUNT(*) FROM policy_settings")[0][0]
        n_audit_after_first = _query(url, "SELECT COUNT(*) FROM policy_changes")[0][0]

        second = _run_cli("settings", "bootstrap")
        assert second.returncode == 0
        assert "0 inserted" in second.stdout.lower() or "no new keys" in second.stdout.lower()

        n_after_second = _query(url, "SELECT COUNT(*) FROM policy_settings")[0][0]
        n_audit_after_second = _query(url, "SELECT COUNT(*) FROM policy_changes")[0][0]
        assert n_after_first == n_after_second
        assert n_audit_after_first == n_audit_after_second


@pytest.mark.integration
def test_cli_settings_reseed_one_key(env_for_postgres):
    from trading_sandwich.settings import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # bootstrap, then mutate
        _run_cli("settings", "bootstrap")
        asyncio.run(repo.set_setting(
            key="regime.adx_trend_threshold", new_value=33,
            value_type="int", rationale="x", changed_by="claude",
            authority="mcp_default",
        ))

        # reseed
        result = _run_cli("settings", "reseed", "--key", "regime.adx_trend_threshold")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "reseeded" in result.stdout.lower() or "restored" in result.stdout.lower()

        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(20,)]


@pytest.mark.integration
def test_cli_settings_reseed_unknown_key_fails(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _run_cli("settings", "bootstrap")
        result = _run_cli("settings", "reseed", "--key", "totally.bogus")
        assert result.returncode != 0
        assert "no_default" in result.stdout.lower() or "not_in_yaml" in result.stdout.lower() \
               or "no_default" in result.stderr.lower() or "not_in_yaml" in result.stderr.lower()


@pytest.mark.integration
def test_cli_settings_list_prints_keys(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _run_cli("settings", "bootstrap")
        result = _run_cli("settings", "list")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "max_order_usd" in result.stdout
        assert "regime.adx_trend_threshold" in result.stdout


@pytest.mark.integration
def test_cli_settings_list_with_prefix_filters(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _run_cli("settings", "bootstrap")
        result = _run_cli("settings", "list", "--prefix", "position_sizing")
        assert result.returncode == 0
        assert "position_sizing.base_pct" in result.stdout
        assert "max_order_usd" not in result.stdout


@pytest.mark.integration
def test_cli_settings_get_known_key(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _run_cli("settings", "bootstrap")
        result = _run_cli("settings", "get", "regime.adx_trend_threshold")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "regime.adx_trend_threshold" in result.stdout
        assert "20" in result.stdout


@pytest.mark.integration
def test_cli_settings_get_tier1_halal_marked(env_for_postgres):
    """Tier 1 keys should be marked as inviolable in the CLI output."""
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = _run_cli("settings", "get", "max_leverage")
        assert result.returncode == 0
        out = result.stdout.lower()
        assert "tier 1" in out or "halal" in out or "inviolable" in out
