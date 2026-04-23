"""Signal worker. Celery consumer that reads recent features, runs detectors,
applies gating (using Postgres to track last-fired per (symbol, archetype)),
writes a signals row, and schedules outcome measurements.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich.celery_app import app
from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Features as FeaturesORM
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.logging import get_logger
from trading_sandwich.metrics import SIGNALS_FIRED
from trading_sandwich.signals.detectors.trend_pullback import detect_trend_pullback

logger = get_logger(__name__)

LOOKBACK = 30
HORIZONS_SECONDS: dict[str, int] = {"15m": 15 * 60, "1h": 60 * 60}


def _load_policy() -> dict:
    with open("policy.yaml") as f:
        return yaml.safe_load(f)


def _row_to_features(r: FeaturesORM) -> FeaturesRow:
    return FeaturesRow(
        symbol=r.symbol, timeframe=r.timeframe, close_time=r.close_time,
        close_price=r.close_price, ema_21=r.ema_21, rsi_14=r.rsi_14,
        atr_14=r.atr_14, trend_regime=r.trend_regime, vol_regime=r.vol_regime,
        feature_version=r.feature_version,
    )


async def _apply_gating(signal: Signal, policy: dict, session_factory) -> Signal:
    threshold = Decimal(str(policy["per_archetype_confidence_threshold"][signal.archetype]))
    if signal.confidence < threshold:
        return signal.model_copy(update={"gating_outcome": "below_threshold"})

    cooldown_min = policy["per_archetype_cooldown_minutes"][signal.archetype]
    async with session_factory() as session:
        last = (await session.execute(
            select(SignalORM.fired_at)
            .where(
                SignalORM.symbol == signal.symbol,
                SignalORM.archetype == signal.archetype,
                SignalORM.gating_outcome == "claude_triaged",
            )
            .order_by(SignalORM.fired_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    if last is not None and signal.fired_at - last < timedelta(minutes=cooldown_min):
        return signal.model_copy(update={"gating_outcome": "cooldown_suppressed"})
    return signal.model_copy(update={"gating_outcome": "claude_triaged"})


async def _persist_signal(signal: Signal, session_factory) -> None:
    async with session_factory() as session:
        stmt = pg_insert(SignalORM).values(
            signal_id=signal.signal_id, symbol=signal.symbol, timeframe=signal.timeframe,
            archetype=signal.archetype, fired_at=signal.fired_at,
            candle_close_time=signal.candle_close_time,
            trigger_price=signal.trigger_price, direction=signal.direction,
            confidence=signal.confidence, confidence_breakdown=signal.confidence_breakdown,
            gating_outcome=signal.gating_outcome,
            features_snapshot=signal.features_snapshot,
            stop_price=signal.stop_price, target_price=signal.target_price, rr_ratio=signal.rr_ratio,
            detector_version=signal.detector_version,
        ).on_conflict_do_nothing(index_elements=["signal_id"])
        await session.execute(stmt)
        await session.commit()


def _schedule_outcomes(signal: Signal) -> None:
    for horizon, secs in HORIZONS_SECONDS.items():
        app.send_task(
            "trading_sandwich.outcomes.worker.measure_outcome",
            args=[str(signal.signal_id), horizon],
            queue="outcomes",
            countdown=secs,
        )


async def _detect_async(symbol: str, timeframe: str, close_time_iso: str) -> None:
    session_factory = get_session_factory()
    close_time = datetime.fromisoformat(close_time_iso)
    policy = _load_policy()

    async with session_factory() as session:
        rows = (await session.execute(
            select(FeaturesORM)
            .where(
                FeaturesORM.symbol == symbol,
                FeaturesORM.timeframe == timeframe,
                FeaturesORM.close_time <= close_time,
            )
            .order_by(FeaturesORM.close_time.desc())
            .limit(LOOKBACK)
        )).scalars().all()

    if not rows:
        return

    rows = list(reversed(rows))
    features = [_row_to_features(r) for r in rows]

    detected = [detect_trend_pullback(features)]
    for sig in detected:
        if sig is None:
            continue
        gated = await _apply_gating(sig, policy, session_factory)
        await _persist_signal(gated, session_factory)
        if gated.gating_outcome == "claude_triaged":
            _schedule_outcomes(gated)
        SIGNALS_FIRED.labels(
            symbol=sig.symbol, timeframe=sig.timeframe,
            archetype=sig.archetype, gating_outcome=gated.gating_outcome,
        ).inc()


@app.task(name="trading_sandwich.signals.worker.detect_signals")
def detect_signals(symbol: str, timeframe: str, close_time_iso: str) -> None:
    asyncio.run(_detect_async(symbol, timeframe, close_time_iso))
