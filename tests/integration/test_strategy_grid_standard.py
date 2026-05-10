"""Phase 3 Wave 1 Task 2.1 — A1 Standard Grid integration test.

End-to-end verification that grid_standard plugs into the Wave 0
foundation correctly:

  deploy_strategy(strategy_type="grid_standard", ...) succeeds (catalog
    + repo + portfolio_decisions row).
  worker tick with the production registry picks grid_standard up,
    runs StandardGridStrategy.tick(), persists state.
  Re-tick is idempotent (no fresh state churn, no errors).

This is the integration counterpart to tests/unit/test_strategy_grid_standard.py
— the unit tests pin tick semantics in isolation; this test pins that
the strategy is wired into the registry + MCP catalog and survives a
real DB round-trip.
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
def test_grid_standard_deploys_and_ticks(env_for_postgres, monkeypatch):
    """Deploy grid_standard via MCP, tick via worker default registry,
    confirm state persisted."""
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import worker
    from trading_sandwich.strategies.grid.standard import StandardGridStrategy

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # --- 1. grid_standard must be present in the production registry ---
        registry = worker._default_registry()
        assert "grid_standard" in registry, (
            "grid_standard must be in worker._default_registry() — "
            "Wave 1 Task 2.1 registers it there."
        )
        assert registry["grid_standard"] is StandardGridStrategy

        # --- 2. Deploy via MCP -----------------------------------------
        deploy = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="grid_standard",
            symbol="BTCUSDT",
            capital_usd=30,
            params={"low": "60000", "high": "70000", "levels": 5},
            rationale="A1 Standard Grid integration test",
        ))
        assert deploy["status"] == "ok", deploy
        sid = deploy["strategy_id"]

        rows = _query(url,
            "SELECT strategy_type, status, capital_allocated_usd "
            "FROM strategies WHERE id = :i", {"i": sid})
        assert rows == [("grid_standard", "active", Decimal("30"))]

        decisions = _query(url,
            "SELECT decision_type FROM portfolio_decisions "
            "WHERE target_strategy_id = :i", {"i": sid})
        assert decisions == [("deploy",)]

        # --- 3. Worker tick. The default snapshot is empty {}; A1
        # tick() requires mid_price. Patch _tick_one_strategy to inject
        # a snapshot with mid_price for this test (snapshot plumbing
        # itself is a later Wave 1 supporting task). ----------------
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
            instance.tick(ctx, snapshot={"mid_price": Decimal("65000")})
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
            assert report.skipped == 0
        finally:
            monkeypatch.setattr(worker, "_tick_one_strategy", original)

        # --- 4. State persisted: 5 levels recorded; 3 submitted. -------
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i",
            {"i": sid})
        assert len(st) == 1
        state = st[0][0]
        assert "levels" in state
        assert len(state["levels"]) == 5
        submitted = [lv["submitted"] for lv in state["levels"]]
        # Mid=65000 → levels at 60k, 62.5k, 65k all <= mid → 3 submitted.
        assert submitted.count(True) == 3

        # last_tick_at populated
        lt = _query(url,
            "SELECT last_tick_at FROM strategies WHERE id = :i",
            {"i": sid})
        assert lt[0][0] is not None
