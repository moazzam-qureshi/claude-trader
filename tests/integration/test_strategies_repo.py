"""Integration tests for strategies/repo.py — Phase 3 plan Task 1.7.

Pins:
  - create_strategy() inserts a row in `strategies` (status=pending)
  - mark_active() / mark_paused() / mark_winding_down() / mark_completed()
    transition status, validating against the state machine, persisting
    last_tick_at / paused_at / completed_at where appropriate.
  - get_state() / save_state() round-trip the JSONB state on
    `strategy_state`, with optimistic locking via updated_at.
  - Concurrent save_state() with a stale version is rejected.
  - Idempotent upsert: saving the same state twice produces one row.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

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


@pytest.mark.integration
def test_create_strategy_inserts_pending_row(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard",
            symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"low": 60000, "high": 70000, "levels": 5},
            deployed_by="claude",
            prompt_version="abc123",
        ))
        assert isinstance(sid, int)

        rows = _query(
            url,
            "SELECT strategy_type, symbol, status, capital_allocated_usd, "
            "capital_deployed_usd, deployed_by, prompt_version "
            "FROM strategies WHERE id = :i",
            {"i": sid},
        )
        assert rows == [(
            "grid_standard", "BTCUSDT", "pending",
            Decimal("30"), Decimal("0"), "claude", "abc123",
        )]


@pytest.mark.integration
def test_mark_active_transitions_pending_to_active(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        rows = _query(url, "SELECT status FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("active",)]


@pytest.mark.integration
def test_mark_paused_records_paused_at(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))
        asyncio.run(repo.mark_paused(sid))

        rows = _query(
            url,
            "SELECT status, paused_at FROM strategies WHERE id = :i",
            {"i": sid},
        )
        assert rows[0][0] == "paused"
        assert rows[0][1] is not None  # paused_at populated


@pytest.mark.integration
def test_full_lifecycle_pending_active_winddown_completed(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))
        asyncio.run(repo.mark_winding_down(sid))
        asyncio.run(repo.mark_completed(sid))

        rows = _query(
            url,
            "SELECT status, completed_at FROM strategies WHERE id = :i",
            {"i": sid},
        )
        assert rows[0][0] == "completed"
        assert rows[0][1] is not None


@pytest.mark.integration
def test_invalid_transition_rejected(env_for_postgres):
    """Cannot go pending->paused; must deploy through active first."""
    from trading_sandwich.strategies import repo
    from trading_sandwich.strategies.base import InvalidTransitionError

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        with pytest.raises(InvalidTransitionError):
            asyncio.run(repo.mark_paused(sid))


@pytest.mark.integration
def test_mark_errored_from_any_state(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))
        asyncio.run(repo.mark_errored(sid, error_message="ccxt rate limit"))

        rows = _query(
            url,
            "SELECT status, error_message FROM strategies WHERE id = :i",
            {"i": sid},
        )
        assert rows == [("errored", "ccxt rate limit")]


@pytest.mark.integration
def test_save_and_load_state_round_trips(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        first = asyncio.run(repo.save_state(
            sid, {"levels_filled": [60000, 62000], "last_action": "buy"},
            expected_updated_at=None,
        ))
        loaded = asyncio.run(repo.get_state(sid))
        assert loaded.state == {"levels_filled": [60000, 62000], "last_action": "buy"}
        assert loaded.updated_at == first


@pytest.mark.integration
def test_save_state_idempotent_upsert(env_for_postgres):
    """Calling save_state twice on the same strategy produces ONE row,
    not two; second call updates the existing row."""
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        v1 = asyncio.run(repo.save_state(sid, {"v": 1}, expected_updated_at=None))
        v2 = asyncio.run(repo.save_state(sid, {"v": 2}, expected_updated_at=v1))

        rows = _query(
            url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i",
            {"i": sid},
        )
        assert len(rows) == 1
        assert rows[0][0] == {"v": 2}
        assert v2 > v1


@pytest.mark.integration
def test_save_state_optimistic_lock_rejects_stale_version(env_for_postgres):
    """If the caller's expected_updated_at is older than DB's current,
    save_state raises StaleStateError (someone else wrote in between)."""
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        v1 = asyncio.run(repo.save_state(sid, {"v": 1}, expected_updated_at=None))
        # Race: another worker writes v2.
        asyncio.run(repo.save_state(sid, {"v": 2}, expected_updated_at=v1))
        # We come back trying to write with the stale v1 timestamp.
        with pytest.raises(repo.StaleStateError):
            asyncio.run(repo.save_state(sid, {"v": 99}, expected_updated_at=v1))

        rows = _query(
            url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i",
            {"i": sid},
        )
        assert rows[0][0] == {"v": 2}  # not overwritten


@pytest.mark.integration
def test_get_state_returns_none_when_no_state_yet(env_for_postgres):
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        loaded = asyncio.run(repo.get_state(sid))
        assert loaded is None


@pytest.mark.integration
def test_list_active_strategies_returns_active_and_paused(env_for_postgres):
    """list_active() returns strategies in active or paused — the ones
    the worker should still be ticking. Excludes pending/winding/done."""
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        s_pending = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        s_active = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="ETHUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_active))
        s_paused = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="SOLUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_paused))
        asyncio.run(repo.mark_paused(s_paused))
        s_done = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="AVAXUSDT",
            capital_allocated_usd=Decimal("30"),
            params={"levels": 5}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(s_done))
        asyncio.run(repo.mark_winding_down(s_done))
        asyncio.run(repo.mark_completed(s_done))

        rows = asyncio.run(repo.list_active())
        ids = sorted(r.id for r in rows)
        assert ids == sorted([s_active, s_paused])
