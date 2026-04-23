"""Feature worker. Celery consumer that reads a rolling window of raw_candles,
computes Phase 0 indicators, upserts a features row, and dispatches signal detection.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Features, RawCandle
from trading_sandwich.features.compute import compute_atr, compute_ema, compute_rsi
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


async def _compute_async(symbol: str, timeframe: str, close_time_iso: str) -> None:
    session_factory = get_session_factory()
    close_time = datetime.fromisoformat(close_time_iso)

    async with session_factory() as session:
        stmt = (
            select(RawCandle)
            .where(
                RawCandle.symbol == symbol,
                RawCandle.timeframe == timeframe,
                RawCandle.close_time <= close_time,
            )
            .order_by(RawCandle.close_time.desc())
            .limit(WINDOW_SIZE)
        )
        rows = (await session.execute(stmt)).scalars().all()

    if len(rows) < 21:
        logger.info("compute_features_insufficient_history", symbol=symbol, tf=timeframe, rows=len(rows))
        return

    rows = list(reversed(rows))
    df = pd.DataFrame([{
        "close_time": r.close_time,
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
    } for r in rows])

    ema_21 = compute_ema(df["close"], period=21).iloc[-1]
    rsi_14 = compute_rsi(df["close"], period=14).iloc[-1]
    atr_14 = compute_atr(df["high"], df["low"], df["close"], period=14).iloc[-1]

    values = {
        "symbol": symbol, "timeframe": timeframe,
        "close_time": close_time,
        "close_price": Decimal(str(df["close"].iloc[-1])),
        "ema_21": None if pd.isna(ema_21) else Decimal(str(ema_21)),
        "rsi_14": None if pd.isna(rsi_14) else Decimal(str(rsi_14)),
        "atr_14": None if pd.isna(atr_14) else Decimal(str(atr_14)),
        "feature_version": _FEATURE_VERSION,
    }

    async with session_factory() as session:
        stmt = pg_insert(Features).values(**values).on_conflict_do_update(
            index_elements=["symbol", "timeframe", "close_time"],
            set_={k: values[k] for k in ("close_price", "ema_21", "rsi_14", "atr_14", "feature_version")},
        )
        await session.execute(stmt)
        await session.commit()

    FEATURES_COMPUTED.labels(symbol=symbol, timeframe=timeframe).inc()
    logger.info("features_computed", symbol=symbol, tf=timeframe, close_time=close_time_iso)

    # Local import avoids a circular import at module load: signals.worker
    # itself imports celery_app (which includes features.worker in `include=`).
    from trading_sandwich.signals.worker import detect_signals as detect_signals_task
    detect_signals_task.apply_async(
        args=[symbol, timeframe, close_time_iso], queue="signals",
    )


@app.task(name="trading_sandwich.features.worker.compute_features")
def compute_features(symbol: str, timeframe: str, close_time_iso: str) -> None:
    with FEATURE_COMPUTE_SECONDS.labels(symbol=symbol, timeframe=timeframe).time():
        run_coro(_compute_async(symbol, timeframe, close_time_iso))
