"""End-to-end paper test for the strategy execution path (Blocker A + B).

Deploys A1 Standard Grid in paper mode and walks the whole loop:

  deploy_strategy → worker tick (build_snapshot from raw_candles) →
  grid emits buy LIMITs at rungs <= mid → bridge submits via PaperAdapter
  → `orders` rows + `strategy_orders` rows created (status 'open') →
  drop a 5m candle whose low crosses the bottom rung → paper_match fills
  that order → fill_apply flips levels[0]['filled_buy']=True → next
  worker tick → emit_sells_for_fills emits a sell at rung 1 → bridge
  submits it → a new sell `orders` row (role='exit', grid_level=1).

This passing is the green light to ask the operator about Task 2.30.
(The sell limit's *fill* is not asserted — paper_match doesn't yet know
about OrderIntent.direction so it would model the sell-limit as a long
buy; the strategy goal here is that the sell leg is *placed*.)
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


def _candle(url: str, *, tf: str, i: int, o: str, h: str, lo: str, c: str) -> None:
    ot = _T0 + timedelta(minutes=i)
    _exec(
        url,
        "INSERT INTO raw_candles (symbol, timeframe, open_time, close_time, "
        "open, high, low, close, volume) "
        "VALUES ('BTCUSDT', :tf, :ot, :ct, :o, :h, :lo, :c, 1) "
        "ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET "
        "high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close",
        {"tf": tf, "ot": ot, "ct": ot + timedelta(minutes=1),
         "o": o, "h": h, "lo": lo, "c": c},
    )


@pytest.mark.integration
def test_grid_paper_lifecycle_buy_fill_then_sell_leg(env_for_postgres, monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.paper_match import match_async
    from trading_sandwich.mcp.tools.strategies_command import deploy_strategy
    from trading_sandwich.strategies import worker
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
    monkeypatch.setattr(_policy, "get_execution_mode", lambda: "paper")

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # build_snapshot and paper_match both read 5m. mid = 91 so only
        # the bottom rung (90) gets a buy LIMIT this tick.
        _candle(url, tf="5m", i=5, o="91", h="92", lo="90", c="91")

        # --- deploy A1 grid: rungs at 90/95/100/105/110 ----------------
        dep = asyncio.run(deploy_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT", capital_usd=30,
            params={"low": 90, "high": 110, "levels": 5},
            rationale="paper e2e: A1 grid lifecycle",
        ))
        assert dep["status"] == "ok"
        sid = dep["strategy_id"]

        # --- tick 1: deploy the buy ladder -----------------------------
        r = asyncio.run(worker.tick_all_strategies())  # default registry
        assert r.ticked == 1 and r.errored == 0

        buys = _query(url,
            "SELECT o.order_id, o.status, o.limit_price, so.role, so.grid_level "
            "FROM orders o JOIN strategy_orders so ON so.order_id = o.order_id "
            "WHERE so.strategy_id = :s ORDER BY so.grid_level", {"s": sid})
        # only rung 0 (price 90) is <= mid(91) → one buy-limit order, open
        assert len(buys) == 1
        assert buys[0][3] == "entry" and buys[0][4] == 0
        assert buys[0][1] == "open"
        assert buys[0][2] == Decimal("90")

        # tick 2 must be idempotent — grid sees levels already populated,
        # re-emits the still-resting buy, bridge dedupes on client_order_id
        asyncio.run(worker.tick_all_strategies())
        assert len(_query(url,
            "SELECT order_id FROM orders WHERE symbol = 'BTCUSDT'")) == 1

        # --- drop a 5m candle that crosses the bottom rung (90) --------
        _candle(url, tf="5m", i=10, o="91", h="92", lo="88", c="89")
        filled = asyncio.run(match_async())
        assert filled == 1  # the 90-rung buy fills (low 88 <= 90)

        # confirm the L0 order is filled
        l0 = _query(url,
            "SELECT o.status FROM orders o JOIN strategy_orders so "
            "ON so.order_id = o.order_id WHERE so.strategy_id = :s "
            "AND so.grid_level = 0", {"s": sid})
        assert l0 == [("filled",)]

        # --- fill-back loop flips levels[0]['filled_buy'] --------------
        applied = asyncio.run(apply_strategy_fills())
        assert applied == 1
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :s", {"s": sid})
        levels = st[0][0]["levels"]
        assert levels[0]["filled_buy"] is True
        assert levels[0]["submitted_sell"] is False  # not yet placed

        # --- next tick: emit the sell-against-fill at rung 1 (price 95) -
        asyncio.run(worker.tick_all_strategies())
        sells = _query(url,
            "SELECT o.order_id, o.status, o.limit_price, so.role, so.grid_level "
            "FROM orders o JOIN strategy_orders so ON so.order_id = o.order_id "
            "WHERE so.strategy_id = :s AND so.role = 'exit'", {"s": sid})
        assert len(sells) == 1
        _oid, status, limit_px, role, gl = sells[0]
        assert role == "exit" and gl == 1
        assert limit_px == Decimal("95")
        assert status == "open"  # placed (its fill isn't asserted here)

        # the rung now records the sell as submitted (idempotent next tick)
        st = _query(url,
            "SELECT state FROM strategy_state WHERE strategy_id = :s", {"s": sid})
        assert st[0][0]["levels"][0]["submitted_sell"] is True

        # one more tick → no new orders (the one buy + one sell stand)
        asyncio.run(worker.tick_all_strategies())
        assert len(_query(url,
            "SELECT order_id FROM orders WHERE symbol = 'BTCUSDT'")) == 2
