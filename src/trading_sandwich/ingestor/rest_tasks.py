"""Celery tasks wrapping the REST pollers. Called on cadence by Celery Beat
(schedule configured in celery_app.py).
"""
from __future__ import annotations

from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawFunding, RawLongShortRatio, RawOpenInterest
from trading_sandwich.ingestor.rest_poller import (
    fapi_base_url,
    fetch_funding_rate,
    fetch_long_short_ratio,
    fetch_open_interest,
)
from trading_sandwich.logging import get_logger

logger = get_logger(__name__)


async def _latest_mark_price(client: httpx.AsyncClient, symbol: str) -> Decimal:
    resp = await client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
    resp.raise_for_status()
    return Decimal(str(resp.json()["markPrice"]))


async def _persist(model, rows: list[dict] | dict) -> None:
    if not rows:
        return
    if isinstance(rows, dict):
        rows = [rows]
    session_factory = get_session_factory()
    async with session_factory() as session:
        for row in rows:
            stmt = pg_insert(model).values(**row).on_conflict_do_nothing()
            await session.execute(stmt)
        await session.commit()


async def _poll_funding_async(symbol: str) -> None:
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=10.0) as client:
        rows = await fetch_funding_rate(client, symbol=symbol, limit=100)
    await _persist(RawFunding, rows)
    logger.info("poll_funding_done", symbol=symbol, rows=len(rows))


async def _poll_open_interest_async(symbol: str) -> None:
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=10.0) as client:
        mark = await _latest_mark_price(client, symbol)
        row = await fetch_open_interest(client, symbol=symbol, mark_price=mark)
    await _persist(RawOpenInterest, row)
    logger.info("poll_oi_done", symbol=symbol)


async def _poll_long_short_ratio_async(symbol: str) -> None:
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=10.0) as client:
        rows = await fetch_long_short_ratio(client, symbol=symbol, period="5m", limit=30)
    await _persist(RawLongShortRatio, rows)
    logger.info("poll_lsr_done", symbol=symbol, rows=len(rows))


@app.task(name="trading_sandwich.ingestor.rest_tasks.poll_funding")
def poll_funding(symbol: str) -> None:
    run_coro(_poll_funding_async(symbol))


@app.task(name="trading_sandwich.ingestor.rest_tasks.poll_open_interest")
def poll_open_interest(symbol: str) -> None:
    run_coro(_poll_open_interest_async(symbol))


@app.task(name="trading_sandwich.ingestor.rest_tasks.poll_long_short_ratio")
def poll_long_short_ratio(symbol: str) -> None:
    run_coro(_poll_long_short_ratio_async(symbol))
