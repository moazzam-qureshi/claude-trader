"""CCXT Pro adapter for Binance L2 depth streams. Normalizes updates into
`raw_orderbook_snapshots` row dicts at most every `throttle_ms`.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import ccxt.pro as ccxtpro

from trading_sandwich.logging import get_logger

logger = get_logger(__name__)


def normalize_ccxt_depth(symbol: str, raw: dict) -> dict:
    """Raw CCXT Pro depth → persistable snapshot dict.
    Levels are preserved as list[list[str]] so Postgres JSONB round-trips
    cleanly and Decimal conversion is deferred to the feature-worker.
    """
    ts_ms = raw.get("timestamp")
    if ts_ms is None:
        captured_at = datetime.now(UTC)
    else:
        captured_at = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    return {
        "symbol": symbol,
        "captured_at": captured_at,
        "bids": [[str(p), str(s)] for p, s in raw.get("bids", [])[:20]],
        "asks": [[str(p), str(s)] for p, s in raw.get("asks", [])[:20]],
    }


async def stream_depth(
    symbols: list[str],
    *,
    testnet: bool = False,
    throttle_ms: int = 200,
) -> AsyncIterator[dict]:
    """Yield normalized depth snapshots at most `throttle_ms` apart per symbol.
    CCXT Pro's `watch_order_book_for_symbols` keeps an in-memory book that
    updates on every delta; we emit the 20-level head snapshot at a steady
    cadence rather than on every tick.
    """
    exchange = ccxtpro.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    if testnet:
        exchange.set_sandbox_mode(True)

    last_emit: dict[str, float] = {s: 0.0 for s in symbols}
    throttle_s = throttle_ms / 1000.0

    try:
        while True:
            try:
                ob = await exchange.watch_order_book_for_symbols(
                    [f"{s[:-4]}/{s[-4:]}" for s in symbols], limit=20,
                )
            except Exception as e:
                logger.exception("ws_depth_error", err=str(e))
                await asyncio.sleep(2)
                continue

            ccxt_symbol = ob["symbol"]
            underscore_symbol = ccxt_symbol.replace("/", "")
            now = asyncio.get_event_loop().time()
            if now - last_emit.get(underscore_symbol, 0.0) < throttle_s:
                continue
            last_emit[underscore_symbol] = now
            yield normalize_ccxt_depth(underscore_symbol, ob)
    finally:
        await exchange.close()
