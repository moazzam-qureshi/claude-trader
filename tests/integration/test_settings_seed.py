"""Integration tests for the settings seed bootstrap.

Bootstrap is the *first-boot* initializer that walks `policy.yaml` and
upserts policy_settings rows for every non-Tier-1 leaf value. It also
writes a `policy_changes` audit row per seeded key with
`changed_by='seed', authority='seed', applied=true`.

Idempotency contract: running bootstrap a second time on a populated
table is a no-op (no new policy_settings writes, no new audit rows)
unless `force_reseed_keys` is supplied.

Tier 1 (halal) keys MUST never appear in policy_settings — the seed
walker excludes them outright. This is asserted by every bootstrap
test below.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §12.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

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


def _scalar(async_url: str, sql: str, params: dict | None = None):
    rows = _query(async_url, sql, params)
    assert len(rows) == 1 and len(rows[0]) == 1, f"expected 1x1 result, got {rows!r}"
    return rows[0][0]


# --- Happy path: first-boot bootstrap inserts non-Tier-1 keys --------------


@pytest.mark.integration
def test_bootstrap_first_boot_inserts_tier3_scalar_keys(env_for_postgres):
    """A fresh DB after bootstrap has Tier 3 scalar keys from policy.yaml."""
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        report = asyncio.run(seed.bootstrap())

        assert report.inserted_count > 0
        assert report.reseeded_count == 0
        assert report.skipped_count == 0

        # A handful of known top-level scalar keys are present
        rows = _query(url, "SELECT value, value_type FROM policy_settings WHERE key = :k",
                      {"k": "max_order_usd"})
        assert rows == [(200, "int")]

        rows = _query(url, "SELECT value, value_type FROM policy_settings WHERE key = :k",
                      {"k": "min_minutes_between_triages"})
        assert rows == [(30, "int")]


@pytest.mark.integration
def test_bootstrap_inserts_nested_keys_with_dotted_paths(env_for_postgres):
    """Nested config blocks are flattened to dotted-path keys."""
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        # Nested under regime.* — known scalar key from policy.yaml
        rows = _query(url, "SELECT value, value_type FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(20, "int")]

        # Nested under position_sizing.*
        rows = _query(url, "SELECT value, value_type FROM policy_settings WHERE key = :k",
                      {"k": "position_sizing.base_pct"})
        assert rows == [(0.40, "float")]


@pytest.mark.integration
def test_bootstrap_inserts_tier2_safety_keys(env_for_postgres):
    """Tier 2 keys are seeded; later runtime overrides go through /safety set."""
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        # All four Tier 2 keys present
        rows = _query(url, "SELECT key, value, value_type FROM policy_settings "
                           "WHERE key IN ('max_account_drawdown_pct', "
                           "'max_daily_realized_loss_usd', 'trading_enabled', "
                           "'auto_flatten_on_kill') ORDER BY key")
        assert len(rows) == 4
        keys = [r[0] for r in rows]
        assert keys == [
            "auto_flatten_on_kill",
            "max_account_drawdown_pct",
            "max_daily_realized_loss_usd",
            "trading_enabled",
        ]


@pytest.mark.integration
def test_bootstrap_skips_tier1_halal_keys(env_for_postgres):
    """Tier 1 halal keys MUST NEVER be DB-backed."""
    from trading_sandwich.settings import seed
    from trading_sandwich.settings.keys import TIER1_HALAL_KEYS

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        for halal_key in TIER1_HALAL_KEYS:
            rows = _query(url, "SELECT * FROM policy_settings WHERE key = :k",
                          {"k": halal_key})
            assert rows == [], f"Tier 1 halal key leaked into policy_settings: {halal_key}"

        # Also nothing under universe.tiers.excluded.* or
        # universe.hard_limits.excluded_symbols_locked.*
        leaked = _query(
            url,
            "SELECT key FROM policy_settings WHERE key LIKE 'universe.tiers.excluded%' "
            "OR key LIKE 'universe.hard_limits.excluded_symbols_locked%'",
        )
        assert leaked == [], f"Excluded-universe paths leaked: {leaked!r}"


@pytest.mark.integration
def test_bootstrap_writes_audit_row_per_seeded_key(env_for_postgres):
    """Every successfully-seeded key gets a policy_changes audit row."""
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        report = asyncio.run(seed.bootstrap())

        # Audit count == seeded count
        n_audit = _scalar(url, "SELECT COUNT(*) FROM policy_changes WHERE changed_by = 'seed'")
        assert n_audit == report.inserted_count

        # All audit rows for seed have applied=true and authority='seed'
        bad = _query(url, "SELECT key, applied, authority FROM policy_changes "
                          "WHERE changed_by = 'seed' AND (applied = false OR authority != 'seed')")
        assert bad == []

        # rationale populated
        no_rationale = _scalar(
            url,
            "SELECT COUNT(*) FROM policy_changes WHERE changed_by = 'seed' "
            "AND (rationale IS NULL OR rationale = '')",
        )
        assert no_rationale == 0


# --- Idempotency: re-running bootstrap is a no-op --------------------------


@pytest.mark.integration
def test_bootstrap_is_idempotent(env_for_postgres):
    """Second bootstrap run inserts nothing, writes no new audit rows."""
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        first = asyncio.run(seed.bootstrap())
        n_settings_after_first = _scalar(url, "SELECT COUNT(*) FROM policy_settings")
        n_audit_after_first = _scalar(url, "SELECT COUNT(*) FROM policy_changes")

        second = asyncio.run(seed.bootstrap())
        assert second.inserted_count == 0
        assert second.skipped_count == first.inserted_count
        assert second.reseeded_count == 0

        # Counts unchanged
        n_settings_after_second = _scalar(url, "SELECT COUNT(*) FROM policy_settings")
        n_audit_after_second = _scalar(url, "SELECT COUNT(*) FROM policy_changes")
        assert n_settings_after_second == n_settings_after_first
        assert n_audit_after_second == n_audit_after_first


@pytest.mark.integration
def test_bootstrap_does_not_overwrite_existing_runtime_change(env_for_postgres):
    """If Claude or operator has tuned a value, a re-bootstrap leaves it alone."""
    from trading_sandwich.settings import repo, seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        # Claude tunes adx_trend_threshold via the standard repo path
        asyncio.run(repo.set_setting(
            key="regime.adx_trend_threshold",
            new_value=33,
            value_type="int",
            rationale="adx tighter for choppy regime",
            changed_by="claude",
            authority="mcp_default",
        ))

        # Re-bootstrap should NOT clobber Claude's value
        report = asyncio.run(seed.bootstrap())
        assert report.inserted_count == 0

        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(33,)]


# --- force_reseed_keys -----------------------------------------------------


@pytest.mark.integration
def test_force_reseed_overwrites_named_keys_only(env_for_postgres):
    """`force_reseed_keys=[k]` restores the YAML default for k; others untouched."""
    from trading_sandwich.settings import repo, seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        # Claude tunes two keys
        asyncio.run(repo.set_setting(
            key="regime.adx_trend_threshold", new_value=33, value_type="int",
            rationale="x", changed_by="claude", authority="mcp_default",
        ))
        asyncio.run(repo.set_setting(
            key="position_sizing.base_pct", new_value=0.55, value_type="float",
            rationale="x", changed_by="claude", authority="mcp_default",
        ))

        # Reseed only the regime key
        report = asyncio.run(seed.bootstrap(force_reseed_keys=["regime.adx_trend_threshold"]))
        assert report.reseeded_count == 1
        assert report.inserted_count == 0

        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "regime.adx_trend_threshold"})
        assert rows == [(20,)]  # YAML default

        rows = _query(url, "SELECT value FROM policy_settings WHERE key = :k",
                      {"k": "position_sizing.base_pct"})
        assert rows == [(0.55,)]  # untouched


@pytest.mark.integration
def test_force_reseed_unknown_key_raises(env_for_postgres):
    """Reseeding a key absent from policy.yaml is a deployment error."""
    from trading_sandwich.settings import seed

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        with pytest.raises(seed.NoYamlDefaultError, match="not_in_yaml"):
            asyncio.run(seed.bootstrap(force_reseed_keys=["not_in_yaml"]))


@pytest.mark.integration
def test_force_reseed_tier1_key_rejected(env_for_postgres):
    """Even reseed must NEVER touch Tier 1 halal keys."""
    from trading_sandwich.settings import seed
    from trading_sandwich.settings._halal import HalalViolationError

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        with pytest.raises(HalalViolationError):
            asyncio.run(seed.bootstrap(force_reseed_keys=["max_leverage"]))

        # No row appeared
        rows = _query(url, "SELECT * FROM policy_settings WHERE key = 'max_leverage'")
        assert rows == []
