"""REST backfill helpers + Beat gap-scan job.

`expected_candle_opens` was Phase 0. Phase 1 adds:
- `scan_gaps` Celery Beat task (runs every 5 min): detects missing opens in
  the last 6h per (symbol, timeframe) and enqueues `backfill_candles`.
- `backfill_candles` Celery task: fetches missing klines from Binance REST
  and inserts them, then dispatches `compute_features` for each new close.
- `BACKFILL_COMPLETENESS` Gauge updated per scan so Grafana can visualise
  per-stream fill rate.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich._universe import symbols as _symbols
from trading_sandwich._universe import timeframes as _tfs
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawCandle
from trading_sandwich.ingestor.rest_poller import fapi_base_url
from trading_sandwich.metrics import BACKFILL_COMPLETENESS

_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def expected_candle_opens(start: datetime, end: datetime, timeframe: str) -> list[datetime]:
    """Return the list of expected candle open times in [start, end) for the timeframe."""
    step = timedelta(minutes=_TF_MINUTES[timeframe])
    result: list[datetime] = []
    cur = start
    while cur < end:
        result.append(cur)
        cur += step
    return result


async def _scan_gaps_async(symbol: str, timeframe: str, lookback_hours: int = 6) -> list[datetime]:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now - timedelta(hours=lookback_hours)
    expected = expected_candle_opens(start, now, timeframe)

    session_factory = get_session_factory()
    async with session_factory() as session:
        existing = (await session.execute(
            select(RawCandle.open_time).where(
                RawCandle.symbol == symbol,
                RawCandle.timeframe == timeframe,
                RawCandle.open_time >= start,
                RawCandle.open_time < now,
            )
        )).scalars().all()
    present = set(existing)
    missing = [o for o in expected if o not in present]
    if expected:
        BACKFILL_COMPLETENESS.labels(symbol=symbol, timeframe=timeframe).set(
            (len(expected) - len(missing)) / len(expected)
        )
    return missing


@app.task(name="trading_sandwich.ingestor.backfill.scan_gaps")
def scan_gaps() -> None:
    async def _run():
        for sym in _symbols():
            for tf in _tfs():
                missing = await _scan_gaps_async(sym, tf)
                if missing:
                    backfill_candles.apply_async(
                        args=[sym, tf, [m.isoformat() for m in missing]],
                        queue="features",
                    )
    run_coro(_run())


@app.task(name="trading_sandwich.ingestor.backfill.backfill_candles")
def backfill_candles(symbol: str, timeframe: str, open_times_iso: list[str]) -> None:
    async def _run():
        if not open_times_iso:
            return
        opens = [datetime.fromisoformat(s) for s in open_times_iso]
        start_ms = int(min(opens).timestamp() * 1000)
        end_ms = int(max(opens).timestamp() * 1000) + 60_000
        async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=30.0) as client:
            resp = await client.get("/fapi/v1/klines", params={
                "symbol": symbol, "interval": timeframe,
                "startTime": start_ms, "endTime": end_ms, "limit": 1500,
            })
            resp.raise_for_status()
            batch = resp.json()
        if not batch:
            return
        from trading_sandwich.ingestor.rest_backfill import _to_row
        rows = [_to_row(symbol, timeframe, r) for r in batch]
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(
                pg_insert(RawCandle).values(rows).on_conflict_do_nothing(
                    index_elements=["symbol", "timeframe", "open_time"],
                )
            )
            await session.commit()
        from trading_sandwich.features.worker import compute_features
        for row in rows:
            compute_features.apply_async(
                args=[symbol, timeframe, row["close_time"].isoformat()],
                queue="features",
            )
    run_coro(_run())
