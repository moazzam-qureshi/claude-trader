"""Feature worker. Celery consumer that assembles RawInputs from all the
raw-data tables, invokes the orchestrator, upserts a features row, and
dispatches signal detection.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import (
    Features,
    RawCandle,
    RawFunding,
    RawLongShortRatio,
    RawOpenInterest,
    RawOrderbookSnapshot,
)
from trading_sandwich.features.compute import RawInputs, build_features_row
from trading_sandwich.logging import get_logger
from trading_sandwich.metrics import FEATURE_COMPUTE_SECONDS, FEATURES_COMPUTED

logger = get_logger(__name__)

WINDOW_SIZE = 500


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_FEATURE_VERSION = _git_sha()


async def _load_raw_inputs(
    session_factory, symbol: str, timeframe: str, close_time: datetime,
) -> RawInputs | None:
    async with session_factory() as session:
        candle_rows = (await session.execute(
            select(RawCandle)
            .where(
                RawCandle.symbol == symbol,
                RawCandle.timeframe == timeframe,
                RawCandle.close_time <= close_time,
            )
            .order_by(RawCandle.close_time.desc())
            .limit(WINDOW_SIZE)
        )).scalars().all()

        if len(candle_rows) < 200:
            return None

        funding_rows = (await session.execute(
            select(RawFunding)
            .where(
                RawFunding.symbol == symbol,
                RawFunding.settlement_time <= close_time,
                RawFunding.settlement_time >= close_time - timedelta(hours=30),
            )
            .order_by(RawFunding.settlement_time.asc())
        )).scalars().all()

        oi_rows = (await session.execute(
            select(RawOpenInterest)
            .where(
                RawOpenInterest.symbol == symbol,
                RawOpenInterest.captured_at <= close_time,
                RawOpenInterest.captured_at >= close_time - timedelta(hours=26),
            )
            .order_by(RawOpenInterest.captured_at.asc())
        )).scalars().all()

        lsr_rows = (await session.execute(
            select(RawLongShortRatio)
            .where(
                RawLongShortRatio.symbol == symbol,
                RawLongShortRatio.captured_at <= close_time,
            )
            .order_by(RawLongShortRatio.captured_at.desc())
            .limit(1)
        )).scalars().all()

        ob = (await session.execute(
            select(RawOrderbookSnapshot)
            .where(
                RawOrderbookSnapshot.symbol == symbol,
                RawOrderbookSnapshot.captured_at <= close_time,
            )
            .order_by(RawOrderbookSnapshot.captured_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    candles = list(reversed(candle_rows))
    return RawInputs(
        candles=pd.DataFrame([{
            "close_time": r.close_time,
            "open": float(r.open), "high": float(r.high),
            "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
        } for r in candles]),
        funding=pd.DataFrame([
            {"settlement_time": r.settlement_time, "rate": r.rate} for r in funding_rows
        ]),
        open_interest=pd.DataFrame([
            {"captured_at": r.captured_at, "open_interest_usd": r.open_interest_usd}
            for r in oi_rows
        ]),
        long_short_ratio=pd.DataFrame([
            {"captured_at": r.captured_at, "ratio": r.ratio} for r in lsr_rows
        ]),
        latest_ob_snapshot=(
            {"bids": ob.bids, "asks": ob.asks} if ob is not None else None
        ),
    )


async def _compute_async(symbol: str, timeframe: str, close_time_iso: str) -> None:
    session_factory = get_session_factory()
    close_time = datetime.fromisoformat(close_time_iso)

    inputs = await _load_raw_inputs(session_factory, symbol, timeframe, close_time)
    if inputs is None:
        logger.info("compute_features_insufficient_history",
                    symbol=symbol, tf=timeframe)
        return

    row = build_features_row(symbol, timeframe, close_time, inputs)
    if row is None:
        return

    row["feature_version"] = _FEATURE_VERSION

    async with session_factory() as session:
        update_cols = {k: v for k, v in row.items()
                       if k not in ("symbol", "timeframe", "close_time")}
        stmt = pg_insert(Features).values(**row).on_conflict_do_update(
            index_elements=["symbol", "timeframe", "close_time"],
            set_=update_cols,
        )
        await session.execute(stmt)
        await session.commit()

    FEATURES_COMPUTED.labels(symbol=symbol, timeframe=timeframe).inc()
    logger.info("features_computed", symbol=symbol, tf=timeframe,
                close_time=close_time_iso,
                trend_regime=row["trend_regime"], vol_regime=row["vol_regime"])

    from trading_sandwich.signals.worker import detect_signals as detect_signals_task
    detect_signals_task.apply_async(
        args=[symbol, timeframe, close_time_iso], queue="signals",
    )


@app.task(name="trading_sandwich.features.worker.compute_features")
def compute_features(symbol: str, timeframe: str, close_time_iso: str) -> None:
    with FEATURE_COMPUTE_SECONDS.labels(symbol=symbol, timeframe=timeframe).time():
        run_coro(_compute_async(symbol, timeframe, close_time_iso))
