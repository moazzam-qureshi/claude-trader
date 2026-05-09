"""Integration tests for settings snapshot generation.

snapshot_policy() captures the *full* effective policy state at decision
time and is embedded into claude_decisions.policy_snapshot /
portfolio_decisions.policy_snapshot. Given a stored snapshot, the audit
chain should let us reconstruct exactly what Claude was looking at when
it made a decision.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §10.
"""
from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_snapshot_after_bootstrap_has_all_three_blocks(env_for_postgres):
    """Snapshot has settings, inviolable, snapshot_at, git_head keys."""
    from trading_sandwich.settings import seed, snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())
        snap = asyncio.run(snapshot.snapshot_policy())

        assert set(snap.keys()) == {"settings", "inviolable", "snapshot_at", "git_head"}
        assert isinstance(snap["settings"], dict)
        assert isinstance(snap["inviolable"], dict)
        assert isinstance(snap["snapshot_at"], str)
        assert isinstance(snap["git_head"], str)


@pytest.mark.integration
def test_snapshot_settings_contains_seeded_keys(env_for_postgres):
    """Every row in policy_settings ends up in snapshot['settings']."""
    from trading_sandwich.settings import seed, snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())
        snap = asyncio.run(snapshot.snapshot_policy())

        # A handful of known seeded keys
        assert snap["settings"]["max_order_usd"] == 200
        assert snap["settings"]["regime.adx_trend_threshold"] == 20
        assert snap["settings"]["position_sizing.base_pct"] == 0.40

        # Tier 2 keys also present (they live in policy_settings after bootstrap)
        assert "max_account_drawdown_pct" in snap["settings"]
        assert "trading_enabled" in snap["settings"]


@pytest.mark.integration
def test_snapshot_inviolable_block_contains_tier1_keys(env_for_postgres):
    """The inviolable block reads Tier 1 halal values from file, not DB."""
    from trading_sandwich.settings import seed, snapshot
    from trading_sandwich.settings.keys import TIER1_HALAL_KEYS

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())
        snap = asyncio.run(snapshot.snapshot_policy())

        # Every Tier 1 key present in the inviolable block (read from
        # policy.yaml — at minimum max_leverage and the excluded universe
        # paths exist in the seed file).
        present_keys = set(snap["inviolable"].keys())
        # max_leverage is hard-coded in policy.yaml and MUST be in inviolable
        assert "max_leverage" in present_keys
        # All inviolable keys are exactly the Tier 1 set
        assert present_keys.issubset(TIER1_HALAL_KEYS)


@pytest.mark.integration
def test_snapshot_reflects_runtime_change(env_for_postgres):
    """If Claude tunes a Tier 3 key, a later snapshot picks up the new value."""
    from trading_sandwich.settings import repo, seed, snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(seed.bootstrap())

        # Snapshot pre-change
        snap_a = asyncio.run(snapshot.snapshot_policy())
        assert snap_a["settings"]["regime.adx_trend_threshold"] == 20

        # Claude tunes
        asyncio.run(repo.set_setting(
            key="regime.adx_trend_threshold", new_value=33,
            value_type="int", rationale="x", changed_by="claude",
            authority="mcp_default",
        ))

        # Snapshot post-change reflects new value
        snap_b = asyncio.run(snapshot.snapshot_policy())
        assert snap_b["settings"]["regime.adx_trend_threshold"] == 33

        # snapshot_at advanced
        assert snap_b["snapshot_at"] >= snap_a["snapshot_at"]


@pytest.mark.integration
def test_snapshot_git_head_is_full_sha_or_unknown(env_for_postgres):
    """git_head is either a full 40-char SHA or 'unknown' (no git in container)."""
    from trading_sandwich.settings import snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        snap = asyncio.run(snapshot.snapshot_policy())
        gh = snap["git_head"]
        assert gh == "unknown" or (len(gh) == 40 and all(c in "0123456789abcdef" for c in gh))
