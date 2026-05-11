"""Blocker B 2/3 — the outbound strategy↔execution bridge.

When a strategy's tick() emits OrderIntents, the worker converts each
to an OrderRequest, runs it through the strategy-intent rail subset,
and (if not blocked) submits via the execution adapter — persisting an
`orders` row and a `strategy_orders` row linking the order back to the
strategy (with role + grid_level).

These tests run in paper mode (PaperAdapter): a market intent fills
immediately at the latest 5m candle close; a limit intent is marked
'open' and left for paper_match. A blocked intent (e.g. trading
disabled) produces no order row and a risk_event instead.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from trading_sandwich.strategies.base import (
    OrderIntent, Regime, ReturnExpectation, Strategy, StrategyContext,
)


_T0 = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


def _exec(url: str, sql: str, params: dict | None = None) -> None:
    async def _run():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params or {})
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _query(url: str, sql: str, params: dict | None = None) -> list[tuple]:
    async def _run():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(sql), params or {})
                return [tuple(row) for row in r]
        finally:
            await engine.dispose()
    return asyncio.run(_run())


def _seed_candles(url: str, symbol: str, close: str) -> None:
    """One 5m candle — both build_snapshot and the paper adapter /
    paper_match read 5m (the live ingestor's finest grain)."""
    _exec(
        url,
        "INSERT INTO raw_candles (symbol, timeframe, open_time, close_time, "
        "open, high, low, close, volume) "
        "VALUES (:s, '5m', :ot, :ct, :c, :c, :c, :c, 1)",
        {"s": symbol, "ot": _T0, "ct": _T0 + timedelta(minutes=5), "c": close},
    )


class _OneMarketBuy(Strategy):
    """Emits exactly one market buy intent on the first tick."""

    def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
        if ctx.state.get("done"):
            return []
        ctx.state["done"] = True
        return [OrderIntent(
            symbol=ctx.symbol, order_type="market", size_usd=Decimal("12"),
            client_order_id=f"obx-{ctx.strategy_id}-1", role="entry",
        )]

    def graceful_shutdown(self, ctx):
        return []

    def emergency_stop(self, ctx):
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)


class _OneLimitBuyGridLevel(Strategy):
    def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
        if ctx.state.get("done"):
            return []
        ctx.state["done"] = True
        return [OrderIntent(
            symbol=ctx.symbol, order_type="limit", size_usd=Decimal("6"),
            limit_price=Decimal("90"),
            client_order_id=f"glx-{ctx.strategy_id}-L2",
            role="entry", grid_level=2,
        )]

    def graceful_shutdown(self, ctx):
        return []

    def emergency_stop(self, ctx):
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)


def _paper_mode(monkeypatch):
    from trading_sandwich import _policy
    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
    monkeypatch.setattr(_policy, "get_execution_mode", lambda: "paper")


@pytest.mark.integration
def test_market_intent_becomes_filled_order_and_strategy_order_link(
    env_for_postgres, monkeypatch,
):
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _paper_mode(monkeypatch)
        _seed_candles(url, "BTCUSDT", "100")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="onebuy", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(worker.tick_all_strategies(
            registry={"onebuy": _OneMarketBuy},
        ))
        assert result.ticked == 1

        orders = _query(url,
            "SELECT order_id, symbol, side, status, execution_mode, size_usd, "
            "       avg_fill_price, filled_base, client_order_id "
            "FROM orders")
        assert len(orders) == 1
        oid, sym, side, status, mode, size_usd, avg_px, filled_base, coid = orders[0]
        assert sym == "BTCUSDT" and side == "long"
        assert status == "filled" and mode == "paper"
        assert size_usd == Decimal("12")
        assert avg_px == Decimal("100")
        assert filled_base == Decimal("12") / Decimal("100")
        assert coid == f"obx-{sid}-1"

        links = _query(url,
            "SELECT strategy_id, order_id, role, grid_level FROM strategy_orders")
        assert links == [(sid, oid, "entry", None)]

        # second tick emits nothing (state['done'] set) — still one order
        asyncio.run(worker.tick_all_strategies(registry={"onebuy": _OneMarketBuy}))
        assert len(_query(url, "SELECT order_id FROM orders")) == 1


@pytest.mark.integration
def test_limit_intent_becomes_open_order_with_grid_level(
    env_for_postgres, monkeypatch,
):
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _paper_mode(monkeypatch)
        _seed_candles(url, "BTCUSDT", "100")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="onegrid", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        asyncio.run(worker.tick_all_strategies(
            registry={"onegrid": _OneLimitBuyGridLevel},
        ))

        orders = _query(url,
            "SELECT order_id, status, order_type, limit_price FROM orders")
        assert len(orders) == 1
        oid, status, otype, limit_px = orders[0]
        assert status == "open" and otype == "limit"
        assert limit_px == Decimal("90")

        links = _query(url,
            "SELECT order_id, role, grid_level FROM strategy_orders")
        assert links == [(oid, "entry", 2)]


@pytest.mark.integration
def test_intent_blocked_by_rail_writes_no_order_but_a_risk_event(
    env_for_postgres, monkeypatch,
):
    """trading_enabled=False → rail_trading_enabled blocks the intent.
    No order row; a risk_event row recorded; the worker still ticks
    cleanly (the strategy isn't errored)."""
    from trading_sandwich import _policy
    from trading_sandwich.strategies import repo, worker

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        # paper mode but trading DISABLED
        monkeypatch.setattr(_policy, "is_trading_enabled", lambda: False)
        monkeypatch.setattr(_policy, "get_execution_mode", lambda: "paper")
        _seed_candles(url, "BTCUSDT", "100")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="onebuy", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(worker.tick_all_strategies(
            registry={"onebuy": _OneMarketBuy},
        ))
        assert result.ticked == 1
        assert result.errored == 0

        assert _query(url, "SELECT order_id FROM orders") == []
        assert _query(url, "SELECT order_id FROM strategy_orders") == []
        risk = _query(url,
            "SELECT kind, severity, context FROM risk_events")
        assert len(risk) == 1
        assert "trading" in risk[0][0]
        assert risk[0][2].get("strategy_id") == sid

        row = asyncio.run(repo.get(sid))
        assert row.status.value == "active"  # not errored


@pytest.mark.integration
def test_sell_intent_persists_order_with_direction_sell(env_for_postgres, monkeypatch):
    """A grid sell-against-fill (direction='sell') round-trips: the
    persisted orders row has direction='sell' and side='long' (halal —
    a sell only reduces the long)."""
    from trading_sandwich.strategies import repo, worker

    class _OneSell(Strategy):
        def tick(self, ctx, snapshot):
            if ctx.state.get("done"):
                return []
            ctx.state["done"] = True
            return [OrderIntent(
                symbol=ctx.symbol, order_type="limit", size_usd=Decimal("6"),
                limit_price=Decimal("110"),
                client_order_id=f"sx-{ctx.strategy_id}-L3",
                role="exit", direction="sell", grid_level=3,
            )]

        def graceful_shutdown(self, ctx):
            return []

        def emergency_stop(self, ctx):
            return []

        def expected_return_for_regime(self, regime):
            return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _paper_mode(monkeypatch)
        _seed_candles(url, "BTCUSDT", "100")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="onesell", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))
        asyncio.run(worker.tick_all_strategies(registry={"onesell": _OneSell}))

        rows = _query(url,
            "SELECT o.side, o.direction, o.order_type, so.role, so.grid_level "
            "FROM orders o JOIN strategy_orders so ON so.order_id = o.order_id "
            "WHERE so.strategy_id = :s", {"s": sid})
        assert len(rows) == 1
        side, direction, otype, role, gl = rows[0]
        assert side == "long"          # halal — position side unchanged
        assert direction == "sell"     # trade direction
        assert otype == "limit" and role == "exit" and gl == 3


@pytest.mark.integration
def test_intent_over_max_order_usd_is_blocked(env_for_postgres, monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.strategies import repo, worker

    class _HugeBuy(Strategy):
        def tick(self, ctx, snapshot):
            return [OrderIntent(
                symbol=ctx.symbol, order_type="market",
                size_usd=Decimal("999999"),
                client_order_id=f"huge-{ctx.strategy_id}", role="entry",
            )]

        def graceful_shutdown(self, ctx):
            return []

        def emergency_stop(self, ctx):
            return []

        def expected_return_for_regime(self, regime):
            return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
        monkeypatch.setattr(_policy, "get_execution_mode", lambda: "paper")
        _seed_candles(url, "BTCUSDT", "100")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="huge", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        asyncio.run(worker.tick_all_strategies(registry={"huge": _HugeBuy}))
        assert _query(url, "SELECT order_id FROM orders") == []
        risk = _query(url, "SELECT kind FROM risk_events")
        assert len(risk) == 1
        assert "max_order_usd" in risk[0][0]
