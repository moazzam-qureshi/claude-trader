"""Phase 3 plan Task 1.13 — Discord /strategies, /regime, /equity, /decisions
handler tests. Pure-handler tests against Postgres; the discord.py
slash-command wrapper has no logic of its own (just delegates to handlers).
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


# --- /strategies list -------------------------------------------------------


@pytest.mark.integration
def test_strategies_list_shows_active_fleet(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h
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

        reply = asyncio.run(h.handle_strategies_list())
        assert "grid_standard" in reply
        assert "BTCUSDT" in reply
        assert "active" in reply
        assert str(sid) in reply


@pytest.mark.integration
def test_strategies_list_empty_says_so(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        reply = asyncio.run(h.handle_strategies_list())
        assert "no" in reply.lower() or "empty" in reply.lower() \
            or "(0 strategies)" in reply


# --- /strategies pause / resume --------------------------------------------


@pytest.mark.integration
def test_strategies_pause_transitions_and_acks(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h
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

        reply = asyncio.run(h.handle_strategies_pause(
            strategy_id=sid, reason="checking grid range",
        ))
        assert "paused" in reply.lower()

        rows = _query(url, "SELECT status FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("paused",)]


@pytest.mark.integration
def test_strategies_pause_invalid_transition_returns_error(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Pending strategy can't be paused.
        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        reply = asyncio.run(h.handle_strategies_pause(
            strategy_id=sid, reason="x",
        ))
        assert "error" in reply.lower() or "cannot" in reply.lower()


@pytest.mark.integration
def test_strategies_resume_transitions_and_acks(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))
        asyncio.run(repo.mark_paused(sid))

        reply = asyncio.run(h.handle_strategies_resume(
            strategy_id=sid, rationale="ATR confirmed",
        ))
        assert "active" in reply.lower() or "resumed" in reply.lower()


# --- /regime override -------------------------------------------------------


@pytest.mark.integration
def test_regime_override_writes_pivot_via_operator_path(env_for_postgres):
    """Operator-driven /regime override writes triggered_by=
    'operator_override' (vs Claude's 'claude_override' from the MCP tool)."""
    from trading_sandwich.discord import strategies_handlers as h

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        reply = asyncio.run(h.handle_regime_override(
            actor_id="operator-id",
            operator_id="operator-id",
            symbol="BTCUSDT",
            regime="range_volatile",
            duration_hours=48,
            rationale="manual call: chop expected after failed breakout",
        ))
        assert "range_volatile" in reply.lower()

        rows = _query(url,
            "SELECT to_regime, triggered_by FROM regime_pivots WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert rows == [("range_volatile", "operator_override")]


@pytest.mark.integration
def test_regime_override_non_operator_rejected(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        reply = asyncio.run(h.handle_regime_override(
            actor_id="some-rando",
            operator_id="operator-id",
            symbol="BTCUSDT",
            regime="range_volatile",
            duration_hours=48,
            rationale="x",
        ))
        assert "not_operator" in reply.lower() or "not authorized" in reply.lower()

        rows = _query(url,
            "SELECT * FROM regime_pivots WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert rows == []


# --- /equity ----------------------------------------------------------------


@pytest.mark.integration
def test_equity_summary_aggregates_allocation(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h
    from trading_sandwich.strategies import repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        for stype, sym, cap in [
            ("grid_standard", "BTCUSDT", 50),
            ("dca_calendar", "ETHUSDT", 30),
        ]:
            sid = asyncio.run(repo.create_strategy(
                strategy_type=stype, symbol=sym,
                capital_allocated_usd=Decimal(cap),
                params={}, deployed_by="claude",
            ))
            asyncio.run(repo.mark_active(sid))

        reply = asyncio.run(h.handle_equity())
        assert "80" in reply       # total
        assert "BTCUSDT" in reply
        assert "ETHUSDT" in reply


# --- /decisions last <duration> --------------------------------------------


@pytest.mark.integration
def test_decisions_last_returns_recent_portfolio_decisions(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h
    from trading_sandwich.mcp.tools import strategies_command

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Generate a couple of decisions via the MCP active commands.
        asyncio.run(strategies_command.deploy_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_usd=30, params={"levels": 5},
            rationale="favorable regime + tight ATR",
        ))
        asyncio.run(strategies_command.override_regime(
            symbol="ETHUSDT", regime="trend_up",
            duration_hours=48,
            rationale="HTF reclaim confirmed; force trend_up",
        ))

        reply = asyncio.run(h.handle_decisions_last(duration="24h"))
        assert "deploy" in reply.lower()
        assert "override" in reply.lower()
        # Both rationales surfaced
        assert "favorable regime" in reply.lower() or "favorable regime + tight ATR" in reply
        assert "htf reclaim" in reply.lower() or "HTF reclaim" in reply


@pytest.mark.integration
def test_decisions_last_empty_window_says_so(env_for_postgres):
    from trading_sandwich.discord import strategies_handlers as h

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        reply = asyncio.run(h.handle_decisions_last(duration="24h"))
        assert "no" in reply.lower() or "(0)" in reply or "empty" in reply.lower()
