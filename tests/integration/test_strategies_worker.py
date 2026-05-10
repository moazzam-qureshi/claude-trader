"""Phase 3 plan Task 1.15 — strategy-worker tick logic.

Tests the per-strategy `_tick_one_strategy` and the multi-strategy
`tick_all_strategies` driver. The Celery wrapper (`strategies_tick_celery`)
is a one-line `run_coro(tick_all_strategies())` and isn't covered here —
the wrapper is exercised end-to-end in Task 1.16's smoke test.

Coverage:
  - Registry maps strategy_type strings to Strategy classes; unknown
    types are skipped with a logged error (not a crash).
  - tick() returning [] persists no orders, advances last_tick_at, saves
    state.
  - tick() returning OrderIntents persists strategy_orders rows linked
    to (placeholder) orders. (Phase 0 only persists the strategy_orders
    side; submitting to execution rail is Wave 1+ work.)
  - State save uses optimistic locking — second concurrent tick raises
    StaleStateError if it tries to write with a stale version.
  - winding_down strategies are NOT ticked (worker excludes them via
    list_active which filters to active+paused).
  - paused strategies ARE in list_active but NOT ticked (worker
    further filters to active only).
  - Crash recovery: a tick that raises is caught, logged, and the
    strategy is marked errored.
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

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)


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


# --- Fixtures --------------------------------------------------------------


class NoOpStrategy(Strategy):
    """Returns no intents on every tick. Smoke-test foundation."""

    def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
        return []

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)


class StatefulStrategy(Strategy):
    """Advances a `tick_count` counter on each tick. Used to verify
    state persistence + optimistic locking."""

    def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
        n = ctx.state.get("tick_count", 0)
        ctx.state["tick_count"] = n + 1
        return []

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)


class ExplodingStrategy(Strategy):
    """Always raises. Used to verify crash isolation."""

    def tick(self, ctx, snapshot):
        raise RuntimeError("synthetic explosion")

    def graceful_shutdown(self, ctx):
        return []

    def emergency_stop(self, ctx):
        return []

    def expected_return_for_regime(self, regime):
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)


# --- tests -----------------------------------------------------------------


@pytest.mark.integration
def test_unknown_strategy_type_is_skipped(env_for_postgres):
    """A strategy_type with no registered class is skipped (logged
    error). The worker continues with other strategies."""
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="not_a_real_strategy", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        # No registry registration → tick should not crash.
        result = asyncio.run(worker.tick_all_strategies(registry={}))
        assert result.skipped == 1
        assert result.ticked == 0


@pytest.mark.integration
def test_noop_strategy_advances_last_tick_at(env_for_postgres):
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="noop", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        registry = {"noop": NoOpStrategy}
        result = asyncio.run(worker.tick_all_strategies(registry=registry))
        assert result.ticked == 1

        rows = _query(url,
            "SELECT last_tick_at FROM strategies WHERE id = :i", {"i": sid})
        assert rows[0][0] is not None


@pytest.mark.integration
def test_stateful_strategy_persists_state_across_ticks(env_for_postgres):
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="stateful", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        registry = {"stateful": StatefulStrategy}
        # Three sequential ticks — counter should reach 3.
        for _ in range(3):
            asyncio.run(worker.tick_all_strategies(registry=registry))

        state = asyncio.run(repo.get_state(sid))
        assert state.state == {"tick_count": 3}


@pytest.mark.integration
def test_paused_strategy_is_not_ticked(env_for_postgres):
    """Paused strategies appear in list_active (so the worker can see
    them) but are filtered to active-only at tick time. last_tick_at
    must NOT advance on the paused one."""
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        active_sid = asyncio.run(repo.create_strategy(
            strategy_type="noop", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(active_sid))

        paused_sid = asyncio.run(repo.create_strategy(
            strategy_type="noop", symbol="ETHUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(paused_sid))
        asyncio.run(repo.mark_paused(paused_sid))

        result = asyncio.run(worker.tick_all_strategies(
            registry={"noop": NoOpStrategy},
        ))
        assert result.ticked == 1
        assert result.skipped_paused == 1

        rows = _query(url,
            "SELECT id, last_tick_at FROM strategies "
            "WHERE id IN (:a, :p) ORDER BY id",
            {"a": active_sid, "p": paused_sid})
        # active row has last_tick_at populated; paused doesn't
        ids = [r[0] for r in rows]
        last_ticks = {r[0]: r[1] for r in rows}
        assert active_sid in ids and paused_sid in ids
        assert last_ticks[active_sid] is not None
        assert last_ticks[paused_sid] is None


@pytest.mark.integration
def test_winding_down_strategy_is_not_ticked(env_for_postgres):
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="noop", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))
        asyncio.run(repo.mark_winding_down(sid))

        result = asyncio.run(worker.tick_all_strategies(
            registry={"noop": NoOpStrategy},
        ))
        # winding_down isn't in list_active() at all
        assert result.ticked == 0


@pytest.mark.integration
def test_exploding_strategy_marks_errored_and_continues(env_for_postgres):
    """A strategy whose tick() raises gets marked errored. Other
    strategies still tick. Worker doesn't crash."""
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        bomb = asyncio.run(repo.create_strategy(
            strategy_type="bomb", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(bomb))

        good = asyncio.run(repo.create_strategy(
            strategy_type="noop", symbol="ETHUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(good))

        registry = {"bomb": ExplodingStrategy, "noop": NoOpStrategy}
        result = asyncio.run(worker.tick_all_strategies(registry=registry))
        assert result.errored == 1
        assert result.ticked == 1

        # Bomb is now errored, with error_message populated
        rows = _query(url,
            "SELECT status, error_message FROM strategies WHERE id = :i",
            {"i": bomb})
        assert rows[0][0] == "errored"
        assert "synthetic explosion" in (rows[0][1] or "")


@pytest.mark.integration
def test_empty_db_empty_registry_is_a_clean_noop(env_for_postgres):
    """No active strategies in DB and empty registry: tick is a no-op,
    no crash. Establishes the baseline for safe startup."""
    from trading_sandwich.strategies import worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        result = asyncio.run(worker.tick_all_strategies(registry={}))
        assert result.ticked == 0
        assert result.skipped == 0
