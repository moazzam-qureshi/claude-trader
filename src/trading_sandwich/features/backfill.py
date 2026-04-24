"""Phase 1 features backfill. For each raw_candles row with >=200 prior bars of
history available, computes the full Phase 1 features row and upserts. Bypasses
pgbouncer. Typically run once at Phase 1 deploy after REST raw-candle backfill.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.db.models import (
    Features,
    RawCandle,
    RawFunding,
    RawLongShortRatio,
    RawOpenInterest,
)
from trading_sandwich.features.compute import RawInputs, build_features_row
from trading_sandwich.logging import configure_logging, get_logger
from trading_sandwich.metrics import FEATURES_COMPUTED

configure_logging()
logger = get_logger(__name__)

WINDOW_SIZE = 500
MIN_HISTORY = 200    # matches build_features_row's internal min
BATCH_SIZE = 1000


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_FEATURE_VERSION = _git_sha()


async def _backfill_symbol_tf(
    session_factory, symbol: str, timeframe: str,
) -> int:
    """Process every raw_candle for (symbol, timeframe), oldest first. Returns rows inserted."""
    async with session_factory() as session:
        raw_rows = (await session.execute(
            select(RawCandle).where(
                RawCandle.symbol == symbol, RawCandle.timeframe == timeframe,
            ).order_by(RawCandle.close_time.asc())
        )).scalars().all()

        funding_rows = (await session.execute(
            select(RawFunding).where(RawFunding.symbol == symbol)
            .order_by(RawFunding.settlement_time.asc())
        )).scalars().all()
        oi_rows = (await session.execute(
            select(RawOpenInterest).where(RawOpenInterest.symbol == symbol)
            .order_by(RawOpenInterest.captured_at.asc())
        )).scalars().all()
        lsr_rows = (await session.execute(
            select(RawLongShortRatio).where(RawLongShortRatio.symbol == symbol)
            .order_by(RawLongShortRatio.captured_at.asc())
        )).scalars().all()

    funding_df = pd.DataFrame([
        {"settlement_time": r.settlement_time, "rate": r.rate} for r in funding_rows
    ])
    oi_df = pd.DataFrame([
        {"captured_at": r.captured_at, "open_interest_usd": r.open_interest_usd}
        for r in oi_rows
    ])
    lsr_df = pd.DataFrame([
        {"captured_at": r.captured_at, "ratio": r.ratio} for r in lsr_rows
    ])

    inserted = 0
    pending: list[dict] = []

    # Start at MIN_HISTORY - 1 (the first index where we have enough history);
    # window of up to WINDOW_SIZE bars gives the orchestrator plenty of context.
    for i in range(MIN_HISTORY - 1, len(raw_rows)):
        window = raw_rows[max(0, i - WINDOW_SIZE + 1): i + 1]
        candles_df = pd.DataFrame([{
            "close_time": r.close_time,
            "open": float(r.open), "high": float(r.high),
            "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
        } for r in window])

        current = raw_rows[i]
        close_time = current.close_time

        inputs = RawInputs(
            candles=candles_df,
            funding=(
                funding_df[funding_df["settlement_time"] <= close_time]
                if not funding_df.empty else funding_df
            ),
            open_interest=(
                oi_df[oi_df["captured_at"] <= close_time]
                if not oi_df.empty else oi_df
            ),
            long_short_ratio=(
                lsr_df[lsr_df["captured_at"] <= close_time].tail(1)
                if not lsr_df.empty else lsr_df
            ),
            latest_ob_snapshot=None,
        )
        row = build_features_row(symbol, timeframe, close_time, inputs)
        if row is None:
            continue
        row["feature_version"] = _FEATURE_VERSION
        pending.append(row)

        if len(pending) >= BATCH_SIZE:
            inserted += await _flush(session_factory, pending)
            pending.clear()

    if pending:
        inserted += await _flush(session_factory, pending)

    # Same-process loop so incrementing the counter in one shot is fine
    FEATURES_COMPUTED.labels(symbol=symbol, timeframe=timeframe).inc(inserted)
    logger.info("features_backfill_done", symbol=symbol, timeframe=timeframe,
                rows=inserted)
    return inserted


async def _flush(session_factory, rows: list[dict]) -> int:
    async with session_factory() as session:
        for row in rows:
            update_cols = {k: v for k, v in row.items()
                           if k not in ("symbol", "timeframe", "close_time")}
            stmt = pg_insert(Features).values(**row).on_conflict_do_update(
                index_elements=["symbol", "timeframe", "close_time"],
                set_=update_cols,
            )
            await session.execute(stmt)
        await session.commit()
    return len(rows)


async def run_features_backfill(*, symbols: list[str], timeframes: list[str]) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    for symbol in symbols:
        for tf in timeframes:
            await _backfill_symbol_tf(session_factory, symbol, tf)
    await engine.dispose()


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--timeframes", default="")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    if args.symbols and args.timeframes:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    else:
        from trading_sandwich._universe import symbols as u_symbols
        from trading_sandwich._universe import timeframes as u_timeframes
        symbols, timeframes = u_symbols(), u_timeframes()
    asyncio.run(run_features_backfill(symbols=symbols, timeframes=timeframes))


if __name__ == "__main__":
    main()
