"""Phase 3 Wave 1 Task 2.11 — B3 Volatility-Adjusted DCA integration test."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
def test_dca_volatility_adj_deploys_and_ticks(env_for_postgres, monkeypatch):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import worker
    from trading_sandwich.strategies.dca.volatility_adj import (
        VolatilityAdjustedDcaStrategy,
    )

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        registry = worker._default_registry()
        assert "dca_volatility_adj" in registry
        assert registry["dca_volatility_adj"] is VolatilityAdjustedDcaStrategy

        deploy = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="dca_volatility_adj",
            symbol="BTCUSDT",
            capital_usd=2000,
            params={
                "base_contribution_usd": "20",
                "interval_seconds": 604800,
                "vol_multiplier_max": "3.0",
            },
            rationale="B3 Volatility-Adjusted DCA integration test",
        ))
        assert deploy["status"] == "ok", deploy
        sid = deploy["strategy_id"]

        original = worker._tick_one_strategy
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)

        async def _tick_with_snapshot(row, cls):
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
            instance.tick(ctx, snapshot={
                "now": now, "atr_percentile": Decimal("100"),  # max vol
            })
            await repo.save_state(
                row.id, ctx.state, expected_updated_at=expected_updated_at,
            )
            await repo.update_last_tick_at(row.id)
            return True

        monkeypatch.setattr(worker, "_tick_one_strategy", _tick_with_snapshot)
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
        assert state["buy_count"] == 1
        # base 20 * max 3 at pct 100 → 60
        assert Decimal(state["total_contributed_usd"]) == Decimal("60")
        assert state["last_buy_at"] == now.isoformat()
