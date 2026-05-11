"""Blocker B 3/3 — the inbound fill-back loop.

When an order linked to a strategy fills, the strategy's
`strategy_state` must reflect it: a grid buy fill flips
`state['levels'][i]['filled_buy'] = True` so the next tick places the
paired sell; a reverse-grid sell fill flips
`state['levels'][i]['filled_sell'] = True` so the next tick places the
rebuy. (Position-units correction for the DCA/rebalance families — they
estimate units as size_usd/price — is out of scope for this first cut;
those orders carry no grid_level and the loop skips them.)

The loop is idempotent: a rung already marked filled is not re-written
(so no spurious optimistic-lock churn). It runs as a Celery beat task,
the same shape as paper_match.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

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


def _insert_strategy(url: str, *, sid: int, stype: str, state: dict) -> None:
    _exec(
        url,
        "INSERT INTO strategies (id, strategy_type, symbol, status, "
        "capital_allocated_usd, capital_deployed_usd, params, deployed_by, "
        "deployed_at) VALUES (:id, :t, 'BTCUSDT', 'active', 30, 0, "
        "CAST('{}' AS jsonb), 'claude', NOW())",
        {"id": sid, "t": stype},
    )
    _exec(
        url,
        "INSERT INTO strategy_state (strategy_id, state, updated_at) "
        "VALUES (:id, CAST(:s AS jsonb), NOW())",
        {"id": sid, "s": json.dumps(state)},
    )


def _insert_order(url: str, *, oid, coid: str, status: str,
                  filled_base: str | None = None) -> None:
    _exec(
        url,
        "INSERT INTO orders (order_id, client_order_id, symbol, side, "
        "order_type, size_usd, size_base, status, execution_mode, "
        "submitted_at, filled_at, filled_base, stop_loss, policy_version) "
        "VALUES (:oid, :coid, 'BTCUSDT', 'long', 'limit', 6, :fb, :st, "
        "'paper', :now, :fa, :fb, CAST('{}' AS jsonb), 'test')",
        {"oid": oid, "coid": coid, "fb": filled_base, "st": status,
         "now": _T0, "fa": _T0 if status == "filled" else None},
    )


def _link(url: str, *, sid: int, oid, role: str, grid_level: int | None) -> None:
    _exec(
        url,
        "INSERT INTO strategy_orders (strategy_id, order_id, role, grid_level) "
        "VALUES (:sid, :oid, :role, :gl)",
        {"sid": sid, "oid": oid, "role": role, "gl": grid_level},
    )


def _grid_levels(prices: list[str]) -> list[dict]:
    return [
        {"price": p, "side": "buy", "submitted": True, "filled_buy": False,
         "submitted_sell": False, "client_order_id": f"gridstd-1-L{i}-entry"}
        for i, p in enumerate(prices)
    ]


@pytest.mark.integration
def test_grid_buy_fill_flips_filled_buy(env_for_postgres):
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_strategy(url, sid=1, stype="grid_standard",
                         state={"levels": _grid_levels(["90", "95", "100"])})
        oid = uuid4()
        _insert_order(url, oid=oid, coid="gridstd-1-L1-entry", status="filled",
                      filled_base="0.063")
        _link(url, sid=1, oid=oid, role="entry", grid_level=1)

        applied = asyncio.run(apply_strategy_fills())
        assert applied == 1

        st = _query(url, "SELECT state FROM strategy_state WHERE strategy_id = 1")
        levels = st[0][0]["levels"]
        assert levels[1]["filled_buy"] is True
        assert levels[0]["filled_buy"] is False  # untouched
        assert levels[2]["filled_buy"] is False


@pytest.mark.integration
def test_idempotent_no_rewrite_when_already_filled(env_for_postgres):
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        levels = _grid_levels(["90", "95"])
        levels[1]["filled_buy"] = True  # already marked
        _insert_strategy(url, sid=1, stype="grid_standard", state={"levels": levels})
        oid = uuid4()
        _insert_order(url, oid=oid, coid="gridstd-1-L1-entry", status="filled",
                      filled_base="0.063")
        _link(url, sid=1, oid=oid, role="entry", grid_level=1)

        before = _query(url,
            "SELECT updated_at FROM strategy_state WHERE strategy_id = 1")[0][0]
        applied = asyncio.run(apply_strategy_fills())
        assert applied == 0  # nothing to do
        after = _query(url,
            "SELECT updated_at FROM strategy_state WHERE strategy_id = 1")[0][0]
        assert before == after  # no spurious write


@pytest.mark.integration
def test_open_order_not_applied(env_for_postgres):
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_strategy(url, sid=1, stype="grid_standard",
                         state={"levels": _grid_levels(["90", "95"])})
        oid = uuid4()
        _insert_order(url, oid=oid, coid="gridstd-1-L1-entry", status="open")
        _link(url, sid=1, oid=oid, role="entry", grid_level=1)

        assert asyncio.run(apply_strategy_fills()) == 0
        st = _query(url, "SELECT state FROM strategy_state WHERE strategy_id = 1")
        assert st[0][0]["levels"][1]["filled_buy"] is False


@pytest.mark.integration
def test_reverse_grid_sell_fill_flips_filled_sell(env_for_postgres):
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        rev_levels = [
            {"price": p, "side": "sell", "submitted": True,
             "filled_sell": False, "submitted_rebuy": False,
             "client_order_id": f"gridrev-1-L{i}-exit"}
            for i, p in enumerate(["100", "105", "110"])
        ]
        _insert_strategy(url, sid=1, stype="grid_reverse",
                         state={"levels": rev_levels})
        oid = uuid4()
        _insert_order(url, oid=oid, coid="gridrev-1-L0-exit", status="filled",
                      filled_base="0.06")
        _link(url, sid=1, oid=oid, role="exit", grid_level=0)

        assert asyncio.run(apply_strategy_fills()) == 1
        st = _query(url, "SELECT state FROM strategy_state WHERE strategy_id = 1")
        levels = st[0][0]["levels"]
        assert levels[0]["filled_sell"] is True
        assert levels[1]["filled_sell"] is False


@pytest.mark.integration
def test_hodl_nested_grid_buy_fill(env_for_postgres):
    """hodl_plus_plus nests its grid under state['grid']['levels'] —
    the loop finds it there too."""
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_strategy(url, sid=1, stype="hodl_plus_plus",
                         state={"grid": {"levels": _grid_levels(["90", "95"])},
                                "core_units": "0.1"})
        oid = uuid4()
        _insert_order(url, oid=oid, coid="hodlpp-1-grid-L0-entry", status="filled",
                      filled_base="0.063")
        _link(url, sid=1, oid=oid, role="entry", grid_level=0)

        assert asyncio.run(apply_strategy_fills()) == 1
        st = _query(url, "SELECT state FROM strategy_state WHERE strategy_id = 1")
        assert st[0][0]["grid"]["levels"][0]["filled_buy"] is True


@pytest.mark.integration
def test_non_grid_order_skipped(env_for_postgres):
    """A DCA-style fill (no grid_level) is skipped — position-units
    correction is out of scope for this cut."""
    from trading_sandwich.strategies.fill_apply import apply_strategy_fills

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_strategy(url, sid=1, stype="dca_calendar",
                         state={"contributions": 1})
        oid = uuid4()
        _insert_order(url, oid=oid, coid="dcacal-1-entry-0", status="filled",
                      filled_base="0.1")
        _link(url, sid=1, oid=oid, role="entry", grid_level=None)

        assert asyncio.run(apply_strategy_fills()) == 0
