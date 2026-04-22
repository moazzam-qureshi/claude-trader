"""Ingestor entrypoint. Subscribes to Binance, writes raw_candles, publishes
`compute_features` tasks on candle close.
"""
from __future__ import annotations

import asyncio
import contextlib
import signal as os_signal

from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich.celery_app import app as celery_app
from trading_sandwich.config import get_settings
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawCandle
from trading_sandwich.ingestor.binance_stream import stream_candles
from trading_sandwich.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def _write_and_dispatch(session_factory, candle) -> None:
    async with session_factory() as session:
        stmt = pg_insert(RawCandle).values(
            symbol=candle.symbol, timeframe=candle.timeframe,
            open_time=candle.open_time, close_time=candle.close_time,
            open=candle.open, high=candle.high,
            low=candle.low, close=candle.close,
            volume=candle.volume,
            quote_volume=candle.quote_volume,
            trade_count=candle.trade_count,
            taker_buy_base=candle.taker_buy_base,
            taker_buy_quote=candle.taker_buy_quote,
        ).on_conflict_do_nothing(index_elements=["symbol", "timeframe", "open_time"])
        result = await session.execute(stmt)
        await session.commit()
        if result.rowcount:
            celery_app.send_task(
                "trading_sandwich.features.worker.compute_features",
                args=[candle.symbol, candle.timeframe, candle.close_time.isoformat()],
                queue="features",
            )
            logger.info("candle_inserted", symbol=candle.symbol, tf=candle.timeframe,
                        close_time=candle.close_time.isoformat())


async def _consume(symbols, timeframes, testnet, session_factory, stop) -> None:
    async for candle in stream_candles(symbols, timeframes, testnet=testnet):
        if stop.is_set():
            break
        try:
            await _write_and_dispatch(session_factory, candle)
        except Exception as exc:
            logger.exception("ingestor_write_error", err=str(exc),
                             symbol=candle.symbol, tf=candle.timeframe)


async def run() -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    logger.info("ingestor_starting", symbols=settings.universe_symbols,
                timeframes=settings.universe_timeframes, testnet=settings.binance_testnet)

    stop = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("ingestor_stopping")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (os_signal.SIGTERM, os_signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal)

    stream_task = asyncio.create_task(
        _consume(settings.universe_symbols, settings.universe_timeframes,
                 settings.binance_testnet, session_factory, stop)
    )
    await stop.wait()
    stream_task.cancel()


if __name__ == "__main__":
    asyncio.run(run())
