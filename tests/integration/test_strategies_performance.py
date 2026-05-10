"""Phase 3 plan Task 1.10 — performance tracker.

Computes per-strategy realized PnL over a window, compares to the
strategy's expected_return_for_regime, and flags underperformers at
<50% of expected (configurable via policy.yaml::performance_tracker).

Realized PnL only (Phase 0 of the tracker — unrealized PnL needs
current price lookup; deferred). Round-trips are computed from
strategy_orders joined to orders:

  buy_cost_total      = sum(filled_base * avg_fill_price + fees) for entry
  sell_proceeds_total = sum(filled_base * avg_fill_price - fees) for exit
  realized_pnl_usd    = sell_proceeds_total - buy_cost_total

Underperformance flag: realized < expected * threshold_pct.

Tests cover: (a) PnL math is right, (b) flagging fires at the right
threshold, (c) window filtering, (d) zero-trade strategies don't
crash the tracker.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from trading_sandwich.strategies.base import Regime, ReturnExpectation


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


def _exec(async_url: str, sql: str, params: dict | None = None) -> None:
    async def _run():
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params or {})
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _seed_filled_order(
    async_url: str,
    *,
    symbol: str,
    side: str,
    filled_base: Decimal,
    avg_fill_price: Decimal,
    fees_usd: Decimal,
    filled_at: datetime,
) -> uuid.UUID:
    """Insert a filled order row. Returns the order_id."""
    oid = uuid.uuid4()
    _exec(async_url, """
        INSERT INTO orders (
            order_id, client_order_id, symbol, side, order_type,
            size_base, size_usd, stop_loss, take_profit, status,
            execution_mode, submitted_at, filled_at, avg_fill_price,
            filled_base, fees_usd, policy_version
        ) VALUES (
            :oid, :coid, :sym, :side, 'market',
            :fb, :su, '{}', NULL, 'filled',
            'paper', :sub, :fa, :p,
            :fb2, :fees, 'test'
        )
    """, {
        "oid": str(oid),
        "coid": f"test-{oid}",
        "sym": symbol, "side": side,
        "fb": filled_base, "su": filled_base * avg_fill_price,
        "sub": filled_at, "fa": filled_at,
        "p": avg_fill_price, "fb2": filled_base,
        "fees": fees_usd,
    })
    return oid


def _link_to_strategy(
    async_url: str, *, strategy_id: int, order_id: uuid.UUID, role: str
) -> None:
    _exec(async_url, """
        INSERT INTO strategy_orders (strategy_id, order_id, role)
        VALUES (:sid, :oid, :r)
    """, {"sid": strategy_id, "oid": str(order_id), "r": role})


@pytest.mark.integration
def test_realized_pnl_round_trip(env_for_postgres):
    """Buy at $60k for 0.001 BTC, sell at $62k for 0.001 BTC. Cost
    = 60 + 0.5 fees = 60.5. Proceeds = 62 - 0.5 = 61.5. PnL = +1.0."""
    from trading_sandwich.strategies import performance, repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("100"),
            params={}, deployed_by="claude",
        ))
        now = datetime.now(timezone.utc)
        buy = _seed_filled_order(
            url, symbol="BTCUSDT", side="buy",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("60000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=1),
        )
        sell = _seed_filled_order(
            url, symbol="BTCUSDT", side="sell",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("62000"),
            fees_usd=Decimal("0.5"), filled_at=now,
        )
        _link_to_strategy(url, strategy_id=sid, order_id=buy, role="entry")
        _link_to_strategy(url, strategy_id=sid, order_id=sell, role="exit")

        report = asyncio.run(performance.compute_realized_pnl(
            sid, since=now - timedelta(days=30),
        ))
        assert report.entry_cost_usd == Decimal("60.5")
        assert report.exit_proceeds_usd == Decimal("61.5")
        assert report.realized_pnl_usd == Decimal("1.0")
        assert report.entry_count == 1
        assert report.exit_count == 1


@pytest.mark.integration
def test_realized_pnl_no_orders_returns_zero(env_for_postgres):
    """A strategy with no orders yields a zero-everywhere report —
    not a crash."""
    from trading_sandwich.strategies import performance, repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"),
            params={}, deployed_by="claude",
        ))
        report = asyncio.run(performance.compute_realized_pnl(sid))
        assert report.realized_pnl_usd == Decimal("0")
        assert report.entry_count == 0
        assert report.exit_count == 0


@pytest.mark.integration
def test_realized_pnl_filters_by_window(env_for_postgres):
    """Orders outside the `since` window are excluded."""
    from trading_sandwich.strategies import performance, repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("100"),
            params={}, deployed_by="claude",
        ))
        now = datetime.now(timezone.utc)

        # Old (60d ago) round-trip
        b_old = _seed_filled_order(
            url, symbol="BTCUSDT", side="buy",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("60000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=60),
        )
        s_old = _seed_filled_order(
            url, symbol="BTCUSDT", side="sell",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("65000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=58),
        )
        _link_to_strategy(url, strategy_id=sid, order_id=b_old, role="entry")
        _link_to_strategy(url, strategy_id=sid, order_id=s_old, role="exit")

        # Recent (10d ago) round-trip
        b_new = _seed_filled_order(
            url, symbol="BTCUSDT", side="buy",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("60000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=10),
        )
        s_new = _seed_filled_order(
            url, symbol="BTCUSDT", side="sell",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("61000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=8),
        )
        _link_to_strategy(url, strategy_id=sid, order_id=b_new, role="entry")
        _link_to_strategy(url, strategy_id=sid, order_id=s_new, role="exit")

        # 30d window: only the recent round-trip counts.
        report = asyncio.run(performance.compute_realized_pnl(
            sid, since=now - timedelta(days=30),
        ))
        # Recent: cost = 60 + 0.5 = 60.5; proceeds = 61 - 0.5 = 60.5; PnL = 0.
        assert report.realized_pnl_usd == Decimal("0")
        assert report.entry_count == 1
        assert report.exit_count == 1


class _FakeGrid:
    """Toy strategy returning a fixed expected return per regime, used
    to exercise the underperformance-flag logic without touching real
    strategy implementations (those don't exist yet — Wave 1)."""
    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(
            monthly_return_pct=Decimal("0.05"),  # 5%/mo
            confidence=0.8,
        )


@pytest.mark.integration
def test_underperformance_flag_fires_below_threshold(env_for_postgres):
    """Capital $100, expected 5%/mo = $5. Threshold 0.5 → flag if
    realized < $2.50. Realized $1 < $2.50 → flagged."""
    from trading_sandwich.strategies import performance, repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("100"),
            params={}, deployed_by="claude",
        ))
        now = datetime.now(timezone.utc)
        buy = _seed_filled_order(
            url, symbol="BTCUSDT", side="buy",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("60000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=1),
        )
        sell = _seed_filled_order(
            url, symbol="BTCUSDT", side="sell",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("62000"),
            fees_usd=Decimal("0.5"), filled_at=now,
        )
        _link_to_strategy(url, strategy_id=sid, order_id=buy, role="entry")
        _link_to_strategy(url, strategy_id=sid, order_id=sell, role="exit")

        flagged = asyncio.run(performance.evaluate(
            strategy=_FakeGrid(),
            strategy_id=sid,
            current_regime=Regime.RANGE_VOLATILE,
            window_days=30,
            underperformance_threshold_pct=0.5,
        ))
        # Expected = $100 * 0.05 = $5.00 (full month proportional to
        # window_days/30 = 1.0 → $5).
        # Realized = $1.00. Below 50% threshold → flagged.
        assert flagged.is_underperforming is True
        assert flagged.expected_pnl_usd == Decimal("5.00")
        assert flagged.realized_pnl_usd == Decimal("1.0")


@pytest.mark.integration
def test_underperformance_flag_quiet_when_meeting_expectation(env_for_postgres):
    """Realized $5 = expected $5 → not underperforming."""
    from trading_sandwich.strategies import performance, repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("100"),
            params={}, deployed_by="claude",
        ))
        now = datetime.now(timezone.utc)
        # Round-trip yielding +$6 PnL: buy 0.001 @ 60000, sell 0.001 @ 66000,
        # fees 0.5+0.5 = 1.0 → 6.0 - 1.0 = +5.0. Wait, let me redo:
        # cost = 0.001*60000 + 0.5 = 60.5; proceeds = 0.001*66000 - 0.5 = 65.5.
        # PnL = 65.5 - 60.5 = 5.0. Right.
        buy = _seed_filled_order(
            url, symbol="BTCUSDT", side="buy",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("60000"),
            fees_usd=Decimal("0.5"), filled_at=now - timedelta(days=1),
        )
        sell = _seed_filled_order(
            url, symbol="BTCUSDT", side="sell",
            filled_base=Decimal("0.001"), avg_fill_price=Decimal("66000"),
            fees_usd=Decimal("0.5"), filled_at=now,
        )
        _link_to_strategy(url, strategy_id=sid, order_id=buy, role="entry")
        _link_to_strategy(url, strategy_id=sid, order_id=sell, role="exit")

        flagged = asyncio.run(performance.evaluate(
            strategy=_FakeGrid(),
            strategy_id=sid,
            current_regime=Regime.RANGE_VOLATILE,
            window_days=30,
            underperformance_threshold_pct=0.5,
        ))
        assert flagged.is_underperforming is False
        assert flagged.realized_pnl_usd == Decimal("5.0")


@pytest.mark.integration
def test_window_days_scales_expected(env_for_postgres):
    """A 7-day window expects 7/30 of monthly expected return.
    Capital $100, monthly 5% → $5/mo, 7d window → ~$1.17.
    Threshold 0.5 → flag if realized < $0.58."""
    from trading_sandwich.strategies import performance, repo

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="grid_standard", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("100"),
            params={}, deployed_by="claude",
        ))
        # No orders. Realized = 0 < 0.58 → flagged.
        flagged = asyncio.run(performance.evaluate(
            strategy=_FakeGrid(),
            strategy_id=sid,
            current_regime=Regime.RANGE_VOLATILE,
            window_days=7,
            underperformance_threshold_pct=0.5,
        ))
        # Expected = 100 * 0.05 * (7/30) = 1.166...
        assert flagged.expected_pnl_usd == pytest.approx(
            Decimal("1.1666666"), abs=Decimal("0.001")
        )
        assert flagged.is_underperforming is True
