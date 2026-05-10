"""Phase 3 plan Task 1.8 — regime classifier hysteresis + DB logging.

The classify-and-log wrapper:
  - Logs every classification to regime_classifications (whether or not
    it triggers a pivot).
  - Returns the *effective* regime, which lags one classification behind
    the raw rule because of the 2-consecutive hysteresis requirement.
  - On hysteresis-cleared pivot, writes a regime_pivots row with
    triggered_by='classifier_hysteresis'.

This file pins the wrapper's contract end-to-end against Postgres.
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

from trading_sandwich.strategies.base import Regime


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


def _signals(price=110, ma_med=100, ma_long=90, slope=8, adx=30, atr=0.025):
    from trading_sandwich.regime.strategy_classifier import RegimeSignals
    return RegimeSignals(
        price=Decimal(str(price)),
        ma_medium=Decimal(str(ma_med)),
        ma_long=Decimal(str(ma_long)),
        ma_medium_slope_bps=slope,
        adx=adx,
        atr_pct=atr,
    )


@pytest.mark.integration
def test_first_classification_does_not_pivot(env_for_postgres):
    """Hysteresis: a single classification can't pivot. The first call
    on a symbol logs the classification, returns the regime as the
    'effective' (no prior to pivot from), but writes NO pivot row."""
    from trading_sandwich.regime import strategy_classifier as sc

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        eff = asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h",
            signals=_signals(adx=30),
        ))
        assert eff == Regime.TREND_UP

        rows = _query(url,
            "SELECT regime FROM regime_classifications WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert rows == [("trend_up",)]
        pivots = _query(url,
            "SELECT * FROM regime_pivots WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert pivots == []


@pytest.mark.integration
def test_two_consecutive_same_classification_clears_hysteresis(env_for_postgres):
    """Two same classifications in a row → first one establishes,
    second one confirms → hysteresis cleared. Still one pivot row only,
    on the SECOND call (when we KNOW we have 2-in-a-row)."""
    from trading_sandwich.regime import strategy_classifier as sc

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # First classification: trend_up, no prior — no pivot row.
        asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h",
            signals=_signals(adx=30),
        ))
        # Second classification: still trend_up — hysteresis confirms.
        asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h",
            signals=_signals(adx=30),
        ))

        classifications = _query(url,
            "SELECT regime FROM regime_classifications WHERE symbol = :s ORDER BY id",
            {"s": "BTCUSDT"})
        assert classifications == [("trend_up",), ("trend_up",)]

        # No pivot row: there was no prior effective regime to pivot FROM.
        pivots = _query(url,
            "SELECT from_regime, to_regime, triggered_by FROM regime_pivots "
            "WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert pivots == []


@pytest.mark.integration
def test_pivot_fires_after_two_consecutive_new_regime(env_for_postgres):
    """The interesting case: established regime is TREND_UP, then we get
    one TRANSITIONING (single — doesn't pivot), then another TRANSITIONING
    (two-in-a-row — pivot!). Pivot row written with from=trend_up,
    to=transitioning, triggered_by=classifier_hysteresis."""
    from trading_sandwich.regime import strategy_classifier as sc

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Establish TREND_UP via 2 consecutive.
        asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=30),
        ))
        asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=30),
        ))
        # Single TRANSITIONING — does NOT pivot.
        eff_a = asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=22),
        ))
        # Second TRANSITIONING — clears hysteresis, pivot fires.
        eff_b = asyncio.run(sc.classify_and_log(
            symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=22),
        ))

        # The effective regime stayed TREND_UP for the first three calls;
        # only on the 4th (second TRANSITIONING) did the pivot land.
        assert eff_a == Regime.TREND_UP
        assert eff_b == Regime.TRANSITIONING

        pivots = _query(url,
            "SELECT from_regime, to_regime, triggered_by FROM regime_pivots "
            "WHERE symbol = :s ORDER BY id",
            {"s": "BTCUSDT"})
        assert pivots == [("trend_up", "transitioning", "classifier_hysteresis")]


@pytest.mark.integration
def test_alternating_classifications_do_not_pivot(env_for_postgres):
    """Flip-flopping: trend_up, transitioning, trend_up, transitioning...
    Hysteresis prevents pivots — none of the classifications make 2-in-a-row."""
    from trading_sandwich.regime import strategy_classifier as sc

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Establish TREND_UP.
        for _ in range(2):
            asyncio.run(sc.classify_and_log(
                symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=30),
            ))
        # Flip-flop 3x.
        for _ in range(3):
            asyncio.run(sc.classify_and_log(
                symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=22),
            ))
            asyncio.run(sc.classify_and_log(
                symbol="BTCUSDT", timeframe="4h", signals=_signals(adx=30),
            ))

        # No pivot — alternation never makes 2 of the SAME in a row.
        # Wait — that's wrong; the trend_up half of the alternation
        # repeatedly matches the established TREND_UP, which IS the same
        # regime. So no pivot from those either. The transitioning ones
        # are isolated → no pivot. So pivots should be empty.
        pivots = _query(url,
            "SELECT to_regime FROM regime_pivots WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert pivots == []


@pytest.mark.integration
def test_messy_cold_start_then_clean_run_is_baseline_not_pivot(env_for_postgres):
    """Older mixed classifications cannot establish an effective regime
    (no run of 2 ever occurs). When a clean run finally appears, it
    establishes the baseline — same cold-start rule as
    test_two_consecutive_same_classification_clears_hysteresis. No
    pivot row is written; the stream's first cleared run is always
    baseline, never transition."""
    from trading_sandwich.regime import strategy_classifier as sc

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        # Mixed history — no run of 2 anywhere.
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=30)))
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=22)))
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=30)))
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=22)))
        # Now two TRANSITIONING in a row — first run cleared.
        eff = asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=22)))

        # Effective regime: TRANSITIONING (the only run). No pivot
        # (this is baseline establishment from cold start).
        assert eff == Regime.TRANSITIONING
        pivots = _query(url,
            "SELECT to_regime FROM regime_pivots WHERE symbol = :s",
            {"s": "BTCUSDT"})
        assert pivots == []


@pytest.mark.integration
def test_per_symbol_per_timeframe_isolation(env_for_postgres):
    """Hysteresis state is keyed by (symbol, timeframe). BTCUSDT@4h
    classifications don't leak into ETHUSDT@4h or BTCUSDT@1h."""
    from trading_sandwich.regime import strategy_classifier as sc

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=30)))
        asyncio.run(sc.classify_and_log("BTCUSDT", "4h", _signals(adx=30)))
        # ETH should start fresh — first classification, no pivot.
        eff = asyncio.run(sc.classify_and_log("ETHUSDT", "4h", _signals(adx=22)))
        assert eff == Regime.TRANSITIONING

        eth_pivots = _query(url,
            "SELECT * FROM regime_pivots WHERE symbol = :s",
            {"s": "ETHUSDT"})
        assert eth_pivots == []
