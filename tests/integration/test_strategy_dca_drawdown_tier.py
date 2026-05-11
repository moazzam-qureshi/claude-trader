"""Phase 3 Wave 1 Task 2.13 — B7 Drawdown-Tier Accumulation integration test."""
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
def test_dca_drawdown_tier_deploys_and_ticks(env_for_postgres, monkeypatch):
    from trading_sandwich.mcp.tools import strategies_command
    from trading_sandwich.strategies import worker
    from trading_sandwich.strategies.dca.drawdown_tier import (
        DrawdownTierStrategy,
    )

    tiers = [
        {"drawdown_pct": "0.30", "deploy_usd": "50"},
        {"drawdown_pct": "0.50", "deploy_usd": "75"},
    ]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        registry = worker._default_registry()
        assert "dca_drawdown_tier" in registry
        assert registry["dca_drawdown_tier"] is DrawdownTierStrategy

        deploy = asyncio.run(strategies_command.deploy_strategy(
            strategy_type="dca_drawdown_tier",
            symbol="BTCUSDT",
            capital_usd=5000,
            params={"tiers": tiers, "reset_threshold_pct": "0.10"},
            rationale="B7 Drawdown-Tier Accumulation integration test",
        ))
        assert deploy["status"] == "ok", deploy
        sid = deploy["strategy_id"]

        # Two ticks: first sets ATH at 100000, second drops to 65000
        # (35% drawdown → tier 0 fires).
        original = worker._tick_one_strategy
        prices = iter([Decimal("100000"), Decimal("65000")])

        async def _tick_with_price(row, cls):
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
            instance.tick(ctx, snapshot={"mid_price": next(prices)})
            await repo.save_state(
                row.id, ctx.state, expected_updated_at=expected_updated_at,
            )
            await repo.update_last_tick_at(row.id)
            return True

        monkeypatch.setattr(worker, "_tick_one_strategy", _tick_with_price)
        try:
            asyncio.run(worker.tick_all_strategies())  # tick 1: ATH
            asyncio.run(worker.tick_all_strategies())  # tick 2: tier 0 fires
        finally:
            monkeypatch.setattr(worker, "_tick_one_strategy", original)

        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :i",
            {"i": sid})
        state = st[0][0]
        assert Decimal(state["ath"]) == Decimal("100000")
        assert state["fired_tiers"] == [0]
        assert state["buy_count"] == 1
        assert Decimal(state["total_deployed_usd"]) == Decimal("50")
