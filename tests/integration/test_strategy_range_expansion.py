"""Phase 3 Wave 1 Task 2.8 — A8 Range Expansion/Contraction integration test."""
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
def test_range_expansion_deploys_and_ticks(env_for_postgres, monkeypatch):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import worker
    from trading_sandwich.strategies.mean_reversion.range_expansion import (
        RangeExpansionStrategy,
    )

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        registry = worker._default_registry()
        assert "range_expansion_contraction" in registry
        assert registry["range_expansion_contraction"] is RangeExpansionStrategy

        deploy = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="range_expansion_contraction",
            symbol="BTCUSDT",
            capital_usd=100,
            params={
                "base_size_usd": "20",
                "min_size_usd": "5",
                "max_size_usd": "40",
                "rebalance_band_pct": "0.1",
            },
            rationale="A8 Range Expansion integration test",
        ))
        assert deploy["status"] == "ok", deploy
        sid = deploy["strategy_id"]

        original = worker._tick_one_strategy

        async def _tick_with_low_vol(row, cls):
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
                "mid_price": Decimal("60000"),
                "atr_percentile": Decimal("0"),  # deep calm → scale in to max
            })
            await repo.save_state(
                row.id, ctx.state, expected_updated_at=expected_updated_at,
            )
            await repo.update_last_tick_at(row.id)
            return True

        monkeypatch.setattr(worker, "_tick_one_strategy", _tick_with_low_vol)
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
        # base=20, pct=0 → raw target 40, max=40 → 40.
        assert state["position_size_usd"] == "40"
