"""One-shot REST raw-candle backfill. Fetches klines from Binance USD-M REST
and inserts into raw_candles. Bypasses pgbouncer via a direct-to-Postgres engine.

Run:
  docker compose run --rm tools python -m trading_sandwich.ingestor.rest_backfill \
      --symbols BTCUSDT,ETHUSDT --timeframes 5m,15m,1h,4h,1d --days 365
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.db.models import RawCandle
from trading_sandwich.ingestor.rest_poller import fapi_base_url
from trading_sandwich.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

_TF_TO_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_BATCH_LIMIT = 1500


async def _fetch_klines_page(
    client: httpx.AsyncClient, *,
    symbol: str, timeframe: str, start_ms: int, limit: int,
) -> list[list]:
    resp = await client.get("/fapi/v1/klines", params={
        "symbol": symbol, "interval": timeframe,
        "startTime": start_ms, "limit": limit,
    })
    resp.raise_for_status()
    return resp.json()


def _to_row(symbol: str, timeframe: str, raw: list) -> dict:
    return {
        "symbol": symbol, "timeframe": timeframe,
        "open_time":  datetime.fromtimestamp(raw[0]  / 1000, tz=UTC),
        "close_time": datetime.fromtimestamp(raw[6]  / 1000, tz=UTC),
        "open":  Decimal(str(raw[1])),  "high":  Decimal(str(raw[2])),
        "low":   Decimal(str(raw[3])),  "close": Decimal(str(raw[4])),
        "volume": Decimal(str(raw[5])),
        "quote_volume":    Decimal(str(raw[7])),
        "trade_count":     int(raw[8]),
        "taker_buy_base":  Decimal(str(raw[9])),
        "taker_buy_quote": Decimal(str(raw[10])),
    }


async def _ensure_partitions_for_range(
    session_factory, start: datetime, end: datetime,
) -> None:
    """Migration 0009 creates ±6 months of raw_candles partitions at deploy.
    Backfilling older history requires partitions for those months; create any
    missing ones idempotently (no-op if already present).
    """
    cur = datetime(start.year, start.month, 1, tzinfo=UTC)
    end_bound = datetime(end.year, end.month, 1, tzinfo=UTC)
    async with session_factory() as session:
        while cur <= end_bound:
            y, m = cur.year, cur.month
            next_start = (
                datetime(y + 1, 1, 1, tzinfo=UTC) if m == 12
                else datetime(y, m + 1, 1, tzinfo=UTC)
            )
            partition_name = f"raw_candles_{y:04d}_{m:02d}"
            await session.execute(text(
                f"CREATE TABLE IF NOT EXISTS {partition_name} "
                f"PARTITION OF raw_candles "
                f"FOR VALUES FROM ('{cur.isoformat()}') TO ('{next_start.isoformat()}')"
            ))
            cur = next_start
        await session.commit()


async def run_backfill(*, symbols: list[str], timeframes: list[str], days: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    end_time = datetime.now(UTC).replace(second=0, microsecond=0)
    backfill_start = end_time - timedelta(days=days)
    await _ensure_partitions_for_range(session_factory, backfill_start, end_time)
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=30.0) as client:
        for symbol in symbols:
            for tf in timeframes:
                tf_minutes = _TF_TO_MINUTES[tf]
                start_time = end_time - timedelta(days=days)
                cursor_ms = int(start_time.timestamp() * 1000)
                total_inserted = 0
                while cursor_ms < int(end_time.timestamp() * 1000):
                    batch = await _fetch_klines_page(
                        client, symbol=symbol, timeframe=tf,
                        start_ms=cursor_ms, limit=_BATCH_LIMIT,
                    )
                    if not batch:
                        break
                    rows = [_to_row(symbol, tf, r) for r in batch]
                    async with session_factory() as session:
                        stmt = pg_insert(RawCandle).values(rows).on_conflict_do_nothing(
                            index_elements=["symbol", "timeframe", "open_time"],
                        )
                        await session.execute(stmt)
                        await session.commit()
                    total_inserted += len(rows)
                    last_open_ms = batch[-1][0]
                    next_cursor = last_open_ms + tf_minutes * 60_000
                    if next_cursor <= cursor_ms:
                        break
                    cursor_ms = next_cursor
                    await asyncio.sleep(0.25)
                logger.info("rest_backfill_done", symbol=symbol, timeframe=tf,
                            rows=total_inserted)
    await engine.dispose()


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Binance REST raw-candle backfill")
    ap.add_argument("--symbols", required=True, help="Comma-separated (e.g. BTCUSDT,ETHUSDT)")
    ap.add_argument("--timeframes", required=True, help="Comma-separated (e.g. 5m,15m,1h,4h,1d)")
    ap.add_argument("--days", type=int, default=365)
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    asyncio.run(run_backfill(symbols=symbols, timeframes=timeframes, days=args.days))


if __name__ == "__main__":
    main()
