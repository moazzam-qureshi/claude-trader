"""Outcome worker. Measures forward result for a signal at a specified horizon."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawCandle, SignalOutcome
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.logging import get_logger
from trading_sandwich.metrics import OUTCOMES_MEASURED
from trading_sandwich.outcomes.compute import measure_forward

logger = get_logger(__name__)

HORIZON_MINUTES: dict[str, int] = {
    "15m": 15, "1h": 60, "4h": 240, "24h": 1440, "3d": 4320, "7d": 10080,
}


def _reconstruct_atr_from_signal(sig: SignalORM) -> Decimal:
    atr = sig.features_snapshot.get("atr_14")
    if atr is None:
        raise ValueError(f"signal {sig.signal_id} has no atr_14 in snapshot")
    return Decimal(str(atr))


async def _measure_async(signal_id: str, horizon: str) -> None:
    session_factory = get_session_factory()

    async with session_factory() as session:
        sig = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == UUID(signal_id))
        )).scalar_one_or_none()
        if sig is None:
            logger.warning("measure_outcome_signal_not_found", signal_id=signal_id)
            return

        horizon_end = sig.fired_at + timedelta(minutes=HORIZON_MINUTES[horizon])
        candles = (await session.execute(
            select(RawCandle)
            .where(
                RawCandle.symbol == sig.symbol,
                RawCandle.timeframe == sig.timeframe,
                RawCandle.close_time > sig.fired_at,
                RawCandle.close_time <= horizon_end,
            )
            .order_by(RawCandle.close_time.asc())
        )).scalars().all()

    if not candles:
        logger.warning("measure_outcome_no_candles", signal_id=signal_id, horizon=horizon)
        return

    df = pd.DataFrame([{
        "close_time": c.close_time,
        "open": float(c.open), "high": float(c.high),
        "low": float(c.low), "close": float(c.close),
    } for c in candles])

    atr = _reconstruct_atr_from_signal(sig)
    result = measure_forward(
        entry_price=sig.trigger_price,
        direction=sig.direction,
        atr=atr,
        candles=df,
    )

    measured_at = datetime.now(UTC)

    async with session_factory() as session:
        stmt = pg_insert(SignalOutcome).values(
            signal_id=sig.signal_id, horizon=horizon,
            measured_at=measured_at,
            **result,
        ).on_conflict_do_update(
            index_elements=["signal_id", "horizon"],
            set_={"measured_at": measured_at, **result},
        )
        await session.execute(stmt)
        await session.commit()

    OUTCOMES_MEASURED.labels(horizon=horizon).inc()
    logger.info("outcome_measured", signal_id=signal_id, horizon=horizon,
                return_pct=str(result["return_pct"]))


@app.task(
    name="trading_sandwich.outcomes.worker.measure_outcome",
    bind=True,
    autoretry_for=(ValueError,),
    retry_backoff=True,
    max_retries=5,
)
def measure_outcome(self, signal_id: str, horizon: str) -> None:
    asyncio.run(_measure_async(signal_id, horizon))
