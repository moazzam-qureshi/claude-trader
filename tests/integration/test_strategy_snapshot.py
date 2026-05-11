"""Blocker A — snapshot plumbing into the strategy worker.

`build_snapshot(symbol)` reads the latest `features` + `raw_candles` row
for a symbol and returns the dict a deployed strategy expects on each
tick. The worker calls this instead of passing `snapshot={}`.

Pragmatic field set (the bulk of Wave 1 — grids, mean-reversion, DCA,
rebalance, trend-MA):
  mid_price, now, reference_price, rsi, bb_lower, bb_upper,
  atr, atr_pct, atr_percentile, ma_fast, ma_slow, ma_n,
  donchian_high, donchian_low

Warm-up / missing-feature behaviour: a field whose source is NULL falls
back to a sane value (close for EMAs/MAs, ~1% ATR, RSI 50, bands ±2%,
donchian high/low from the recent candle window) so a strategy never
crashes the worker on a missing key. If there's no `raw_candles` row at
all for the symbol, build_snapshot returns None and the worker skips
that strategy (logged, like the unknown-strategy_type path).
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


def _exec(async_url: str, sql: str, params: dict | None = None) -> None:
    async def _run():
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params or {})
        finally:
            await engine.dispose()
    asyncio.run(_run())


_T0 = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


def _insert_candle(url: str, *, symbol: str, tf: str, i: int,
                   open_: str, high: str, low: str, close: str) -> None:
    ot = _T0 + timedelta(minutes=i)
    ct = ot + timedelta(minutes=1)
    _exec(
        url,
        "INSERT INTO raw_candles "
        "(symbol, timeframe, open_time, close_time, open, high, low, close, volume) "
        "VALUES (:s, :tf, :ot, :ct, :o, :h, :l, :c, 1)",
        {"s": symbol, "tf": tf, "ot": ot, "ct": ct,
         "o": open_, "h": high, "l": low, "c": close},
    )


def _insert_features(url: str, *, symbol: str, tf: str, i: int, **cols) -> None:
    ct = _T0 + timedelta(minutes=i) + timedelta(minutes=1)
    keys = ["symbol", "timeframe", "close_time", "close_price", "feature_version"]
    vals = {"symbol": symbol, "timeframe": tf, "close_time": ct,
            "close_price": cols.pop("close_price", "100"),
            "feature_version": "test"}
    for k, v in cols.items():
        keys.append(k)
        vals[k] = v
    placeholders = ", ".join(f":{k}" for k in keys)
    _exec(
        url,
        f"INSERT INTO features ({', '.join(keys)}) VALUES ({placeholders})",
        vals,
    )


@pytest.mark.integration
def test_build_snapshot_reads_latest_features_and_candle(env_for_postgres):
    from trading_sandwich.strategies.snapshot import build_snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Two candles so reference_price (prior close) is well defined.
        _insert_candle(url, symbol="BTCUSDT", tf="1m", i=0,
                       open_="99", high="101", low="98", close="100")
        _insert_candle(url, symbol="BTCUSDT", tf="1m", i=1,
                       open_="100", high="103", low="99", close="102")
        _insert_features(
            url, symbol="BTCUSDT", tf="1m", i=1, close_price="102",
            rsi_14="61.5", bb_lower="95", bb_upper="110",
            atr_14="2.04", atr_percentile_100="42",
            ema_8="101", ema_21="100", ema_55="98", ema_200="95",
            donchian_upper="105", donchian_lower="96",
        )

        snap = asyncio.run(build_snapshot("BTCUSDT"))
        assert snap is not None
        assert snap["mid_price"] == Decimal("102")
        assert snap["reference_price"] == Decimal("100")  # prior bar close
        assert isinstance(snap["now"], datetime)
        assert snap["rsi"] == Decimal("61.5")
        assert snap["bb_lower"] == Decimal("95")
        assert snap["bb_upper"] == Decimal("110")
        assert snap["atr"] == Decimal("2.04")
        assert snap["atr_pct"] == Decimal("2.04") / Decimal("102")
        assert snap["atr_percentile"] == Decimal("42")
        # ema_21 -> fast, ema_55 -> slow, ema_55 -> ma_n (the MA50 proxy)
        assert snap["ma_fast"] == Decimal("100")
        assert snap["ma_slow"] == Decimal("98")
        assert snap["ma_n"] == Decimal("98")
        assert snap["donchian_high"] == Decimal("105")
        assert snap["donchian_low"] == Decimal("96")


@pytest.mark.integration
def test_build_snapshot_returns_none_when_no_candle(env_for_postgres):
    from trading_sandwich.strategies.snapshot import build_snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        assert asyncio.run(build_snapshot("BTCUSDT")) is None


@pytest.mark.integration
def test_build_snapshot_warmup_fallbacks_when_features_null(env_for_postgres):
    """A candle exists but features are NULL (warm-up): the snapshot
    still has every key, with sane fallbacks — close for MAs, ~1% ATR,
    RSI 50, bands ±2%, donchian from the candle's own high/low."""
    from trading_sandwich.strategies.snapshot import build_snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_candle(url, symbol="BTCUSDT", tf="1m", i=0,
                       open_="199", high="205", low="195", close="200")
        # features row exists but every indicator column is NULL
        _insert_features(url, symbol="BTCUSDT", tf="1m", i=0, close_price="200")

        snap = asyncio.run(build_snapshot("BTCUSDT"))
        assert snap is not None
        assert snap["mid_price"] == Decimal("200")
        assert snap["reference_price"] == Decimal("200")  # no prior bar
        assert snap["rsi"] == Decimal("50")
        assert snap["bb_lower"] == Decimal("200") * Decimal("0.98")
        assert snap["bb_upper"] == Decimal("200") * Decimal("1.02")
        assert snap["atr"] == Decimal("200") * Decimal("0.01")
        assert snap["atr_pct"] == Decimal("0.01")
        assert snap["atr_percentile"] == Decimal("50")
        assert snap["ma_fast"] == Decimal("200")
        assert snap["ma_slow"] == Decimal("200")
        assert snap["ma_n"] == Decimal("200")
        # donchian fallback: candle high / low
        assert snap["donchian_high"] == Decimal("205")
        assert snap["donchian_low"] == Decimal("195")


@pytest.mark.integration
def test_build_snapshot_works_without_features_row(env_for_postgres):
    """A candle exists but no features row at all — still produce a
    snapshot from the candle alone (all fallbacks)."""
    from trading_sandwich.strategies.snapshot import build_snapshot

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_candle(url, symbol="ETHUSDT", tf="1m", i=0,
                       open_="9", high="11", low="8", close="10")

        snap = asyncio.run(build_snapshot("ETHUSDT"))
        assert snap is not None
        assert snap["mid_price"] == Decimal("10")
        assert snap["rsi"] == Decimal("50")
        assert snap["ma_fast"] == Decimal("10")
        assert snap["donchian_high"] == Decimal("11")
        assert snap["donchian_low"] == Decimal("8")


@pytest.mark.integration
def test_worker_tick_passes_populated_snapshot_to_strategy(env_for_postgres):
    """The real worker tick (not a monkeypatch) hands the strategy a
    populated snapshot built from raw_candles + features — not `{}`."""
    from trading_sandwich.strategies import repo, worker
    from trading_sandwich.strategies.base import (
        OrderIntent, Regime, ReturnExpectation, Strategy, StrategyContext,
    )

    captured: list[dict] = []

    class SnapshotRecorder(Strategy):
        def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
            captured.append(dict(snapshot))
            return []

        def graceful_shutdown(self, ctx):
            return []

        def emergency_stop(self, ctx):
            return []

        def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
            return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        _insert_candle(url, symbol="BTCUSDT", tf="1m", i=0,
                       open_="99", high="101", low="98", close="100")
        _insert_candle(url, symbol="BTCUSDT", tf="1m", i=1,
                       open_="100", high="103", low="99", close="102")
        _insert_features(url, symbol="BTCUSDT", tf="1m", i=1, close_price="102",
                         rsi_14="60", ema_21="101", ema_55="98", atr_14="2")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="recorder", symbol="BTCUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(worker.tick_all_strategies(
            registry={"recorder": SnapshotRecorder},
        ))
        assert result.ticked == 1
        assert result.skipped_no_data == 0
        assert len(captured) == 1
        snap = captured[0]
        assert snap["mid_price"] == Decimal("102")
        assert snap["rsi"] == Decimal("60")
        assert snap["ma_fast"] == Decimal("101")
        assert "now" in snap and "donchian_high" in snap


@pytest.mark.integration
def test_worker_skips_strategy_with_no_market_data(env_for_postgres):
    """A deployed strategy on a symbol with no raw_candles row is
    skipped (counted as skipped_no_data), not crashed, not errored."""
    from trading_sandwich.strategies import repo, worker
    from trading_sandwich.strategies.base import (
        OrderIntent, Regime, ReturnExpectation, Strategy, StrategyContext,
    )

    class NeedsData(Strategy):
        def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
            return [OrderIntent(
                symbol=ctx.symbol, order_type="market", size_usd=Decimal("1"),
                client_order_id="x", role="entry",
            )]

        def graceful_shutdown(self, ctx):
            return []

        def emergency_stop(self, ctx):
            return []

        def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
            return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.0)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        sid = asyncio.run(repo.create_strategy(
            strategy_type="needs_data", symbol="SOLUSDT",
            capital_allocated_usd=Decimal("30"), params={}, deployed_by="claude",
        ))
        asyncio.run(repo.mark_active(sid))

        result = asyncio.run(worker.tick_all_strategies(
            registry={"needs_data": NeedsData},
        ))
        assert result.ticked == 0
        assert result.errored == 0
        assert result.skipped_no_data == 1

        row = asyncio.run(repo.get(sid))
        assert row.status.value == "active"  # not errored
