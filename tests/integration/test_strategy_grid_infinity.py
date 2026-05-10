"""Phase 3 Wave 1 Task 2.2 — A2 Infinity Grid integration test.

Pins that grid_infinity is wired into the production worker registry,
the MCP catalog, and survives a real DB round-trip.
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
def test_grid_infinity_deploys_and_ticks(env_for_postgres, monkeypatch):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import worker
    from trading_sandwich.strategies.grid.infinity import InfinityGridStrategy

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        registry = worker._default_registry()
        assert "grid_infinity" in registry, (
            "grid_infinity must be in worker._default_registry() — "
            "Wave 1 Task 2.2 registers it there."
        )
        assert registry["grid_infinity"] is InfinityGridStrategy

        deploy = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="grid_infinity",
            symbol="BTCUSDT",
            capital_usd=50,
            params={"low": "100", "step_pct": "0.02", "levels": 5},
            rationale="A2 Infinity Grid integration test",
        ))
        assert deploy["status"] == "ok", deploy
        sid = deploy["strategy_id"]

        rows = _query(url,
            "SELECT strategy_type, status, capital_allocated_usd "
            "FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("grid_infinity", "active", Decimal("50"))]

        # Inject snapshot via _tick_one_strategy patch (worker
        # snapshot plumbing is a later Wave 1 supporting task).
        original = worker._tick_one_strategy

        async def _tick_with_mid(row, cls):
            from trading_sandwich.strategies import repo
            from trading_sandwich.strategies.base import StrategyContext
            instance = cls()
            state_row = await repo.get_state(row.id)
            state = dict(state_row.state) if state_row is not None else {}
            expected_updated_at = (
                state_row.updated_at if state_row is not None else None
            )
            ctx = StrategyContext(
                strategy_id=row.id,
                strategy_type=row.strategy_type,
                symbol=row.symbol,
                params=dict(row.params),
                state=state,
                capital_allocated_usd=row.capital_allocated_usd,
                capital_deployed_usd=row.capital_deployed_usd,
            )
            instance.tick(ctx, snapshot={"mid_price": Decimal("105")})
            await repo.save_state(
                row.id, ctx.state, expected_updated_at=expected_updated_at,
            )
            await repo.update_last_tick_at(row.id)
            return True

        monkeypatch.setattr(worker, "_tick_one_strategy", _tick_with_mid)
        try:
            report = asyncio.run(worker.tick_all_strategies())
            assert report.ticked == 1, report
            assert report.errored == 0
        finally:
            monkeypatch.setattr(worker, "_tick_one_strategy", original)

        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i",
            {"i": sid})
        state = st[0][0]
        assert "levels" in state
        assert "step_pct" in state
        assert state["step_pct"] == "0.02"
        assert len(state["levels"]) == 5
        submitted = [lv["submitted"] for lv in state["levels"]]
        # mid=105 → rungs at 100, 102, 104.04 are <= mid → 3 submitted.
        assert submitted.count(True) == 3
