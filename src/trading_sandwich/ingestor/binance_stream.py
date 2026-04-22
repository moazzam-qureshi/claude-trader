"""Thin CCXT Pro adapter. Normalizes raw payloads into typed Candle DTOs
and yields them on close events.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import ccxt.pro as ccxtpro

from trading_sandwich.contracts.models import Candle
from trading_sandwich.logging import get_logger

logger = get_logger(__name__)

_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def normalize_ccxt_ohlcv(symbol: str, timeframe: str, raw: list) -> Candle:
    ts_ms, o, h, low, c, v = raw
    open_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    close_time = open_time + timedelta(minutes=_TF_MINUTES[timeframe])
    return Candle(
        symbol=symbol, timeframe=timeframe,
        open_time=open_time, close_time=close_time,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(low)), close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


async def stream_candles(
    symbols: list[str],
    timeframes: list[str],
    *,
    testnet: bool = True,
) -> AsyncIterator[Candle]:
    exchange = ccxtpro.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    if testnet:
        exchange.set_sandbox_mode(True)

    last_open: dict[tuple[str, str], datetime] = {}

    async def _watch_loop(sym: str, tf: str, q: asyncio.Queue) -> None:
        while True:
            try:
                ohlcv = await exchange.watch_ohlcv(sym, tf)
                if not ohlcv:
                    continue
                for raw in ohlcv:
                    candle = normalize_ccxt_ohlcv(sym, tf, raw)
                    key = (sym, tf)
                    if last_open.get(key) != candle.open_time:
                        last_open[key] = candle.open_time
                        await q.put(candle)
            except Exception as e:
                logger.exception("ws_watch_error", symbol=sym, timeframe=tf, err=str(e))
                await asyncio.sleep(2)

    q: asyncio.Queue[Candle] = asyncio.Queue(maxsize=1000)
    tasks = [asyncio.create_task(_watch_loop(s, t, q)) for s in symbols for t in timeframes]
    try:
        while True:
            candle = await q.get()
            yield candle
    finally:
        for t in tasks:
            t.cancel()
        await exchange.close()
