"""One-shot microstructure backfill at Phase 1 deploy.

Pulls ~30 days of funding settlements + ~7 days of 1h OI snapshots per symbol
so the features pipeline has meaningful 24h-mean and 24h-delta values from t=0.

Run:
  docker compose run --rm tools python -m \
      trading_sandwich.ingestor.rest_backfill_microstructure --symbols BTCUSDT,ETHUSDT,...
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.db.models import RawFunding, RawOpenInterest
from trading_sandwich.ingestor.rest_poller import fapi_base_url
from trading_sandwich.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def _fetch_funding_window(client: httpx.AsyncClient, symbol: str, days: int) -> list[dict]:
    """GET /fapi/v1/fundingRate — paginates backwards from now."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    resp = await client.get("/fapi/v1/fundingRate", params={
        "symbol": symbol, "limit": 1000,
        "startTime": int(start.timestamp() * 1000),
        "endTime":   int(end.timestamp() * 1000),
    })
    resp.raise_for_status()
    return [
        {"symbol": symbol,
         "settlement_time": datetime.fromtimestamp(r["fundingTime"] / 1000, tz=UTC),
         "rate": Decimal(str(r["fundingRate"]))}
        for r in resp.json()
    ]


async def _fetch_oi_history(client: httpx.AsyncClient, symbol: str, days: int) -> list[dict]:
    """GET /futures/data/openInterestHist — 1h granularity."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    resp = await client.get("/futures/data/openInterestHist", params={
        "symbol": symbol, "period": "1h", "limit": 500,
        "startTime": int(start.timestamp() * 1000),
        "endTime":   int(end.timestamp() * 1000),
    })
    resp.raise_for_status()
    rows = []
    for r in resp.json():
        mark_notional = Decimal(str(r.get("sumOpenInterestValue", r["sumOpenInterest"])))
        rows.append({
            "symbol": symbol,
            "captured_at": datetime.fromtimestamp(r["timestamp"] / 1000, tz=UTC),
            "open_interest_usd": mark_notional,
        })
    return rows


async def run_microstructure_backfill(*, symbols: list[str]) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=30.0) as client:
        for symbol in symbols:
            funding = await _fetch_funding_window(client, symbol, days=30)
            oi = await _fetch_oi_history(client, symbol, days=7)
            async with session_factory() as session:
                if funding:
                    await session.execute(
                        pg_insert(RawFunding).values(funding).on_conflict_do_nothing()
                    )
                if oi:
                    await session.execute(
                        pg_insert(RawOpenInterest).values(oi).on_conflict_do_nothing()
                    )
                await session.commit()
            logger.info("microstructure_backfill_done",
                        symbol=symbol, funding=len(funding), oi=len(oi))

    await engine.dispose()


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    asyncio.run(run_microstructure_backfill(symbols=symbols))


if __name__ == "__main__":
    main()
