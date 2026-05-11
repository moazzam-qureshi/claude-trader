"""Strategy snapshot builder — Blocker A (path to production).

`build_snapshot(symbol)` reads the latest `features` + `raw_candles` row
for a symbol and returns the per-tick `snapshot` dict that deployed
strategies expect. The strategy-worker calls this instead of passing an
empty `{}` — without it a deployed strategy ticks every 30s and can't
compute a single decision.

Field set (the bulk of Wave 1 — grids, mean-reversion, DCA, rebalance,
trend-MA all draw from this; the exotic feeds — multi-timeframe trend
bias, BTC dominance, price z-score — are a follow-up and the few
strategies that need them simply don't fire until then):

    mid_price          latest raw_candles.close
    now                datetime.now(timezone.utc)
    reference_price    prior bar's raw_candles.close (else current close)
    rsi                features.rsi_14            (fallback 50)
    bb_lower/bb_upper  features.bb_lower/bb_upper (fallback close ±2%)
    atr                features.atr_14            (fallback ~1% of price)
    atr_pct            atr / close                (fallback 0.01)
    atr_percentile     features.atr_percentile_100 (fallback 50)
    ma_fast            features.ema_21            (fallback close)
    ma_slow            features.ema_55            (fallback close)
    ma_n               features.ema_55            (the ~MA50 proxy; fallback close)
    donchian_high/low  features.donchian_upper/lower (fallback recent candle hi/lo)

Warm-up safety: a strategy raises `KeyError` on a missing required
snapshot key, which would crash the worker. So every key is always
present — a NULL feature column degrades to a sane fallback rather than
being omitted. If there's no `raw_candles` row at all for the symbol,
build_snapshot returns None and the worker skips that strategy (logged,
like the unknown-strategy_type path).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings


# Primary timeframe the snapshot is built from. The strategy worker
# ticks every 30s; 1m candles are the finest grain the ingestor keeps,
# so they're the freshest read of "where is the price now".
_PRIMARY_TIMEFRAME = "1m"

_DONCHIAN_WINDOW = 20  # bars to scan for the high/low fallback


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def build_snapshot(
    symbol: str, *, timeframe: str = _PRIMARY_TIMEFRAME,
) -> dict[str, Any] | None:
    """Build the per-tick snapshot for `symbol`. Returns None if no
    `raw_candles` row exists for the symbol/timeframe yet (the worker
    treats that as "skip this strategy this tick")."""
    engine = _engine()
    try:
        async with engine.connect() as conn:
            cr = await conn.execute(
                text(
                    "SELECT open, high, low, close, open_time "
                    "FROM raw_candles "
                    "WHERE symbol = :s AND timeframe = :tf "
                    "ORDER BY open_time DESC LIMIT 2"
                ),
                {"s": symbol, "tf": timeframe},
            )
            candles = cr.fetchall()
            if not candles:
                return None
            latest = candles[0]
            prior = candles[1] if len(candles) > 1 else None

            close = _dec(latest.close) or Decimal("0")
            reference_price = _dec(prior.close) if prior is not None else close

            # Donchian fallback: high/low over the recent window.
            dr = await conn.execute(
                text(
                    "SELECT MAX(high) AS hi, MIN(low) AS lo FROM ("
                    "  SELECT high, low FROM raw_candles "
                    "  WHERE symbol = :s AND timeframe = :tf "
                    "  ORDER BY open_time DESC LIMIT :n"
                    ") w"
                ),
                {"s": symbol, "tf": timeframe, "n": _DONCHIAN_WINDOW},
            )
            drow = dr.first()
            window_high = _dec(drow.hi) if drow is not None else _dec(latest.high)
            window_low = _dec(drow.lo) if drow is not None else _dec(latest.low)

            fr = await conn.execute(
                text(
                    "SELECT rsi_14, bb_lower, bb_upper, atr_14, "
                    "       atr_percentile_100, ema_21, ema_55, "
                    "       donchian_upper, donchian_lower "
                    "FROM features "
                    "WHERE symbol = :s AND timeframe = :tf "
                    "ORDER BY close_time DESC LIMIT 1"
                ),
                {"s": symbol, "tf": timeframe},
            )
            f = fr.first()
    finally:
        await engine.dispose()

    rsi = _dec(f.rsi_14) if f is not None else None
    bb_lower = _dec(f.bb_lower) if f is not None else None
    bb_upper = _dec(f.bb_upper) if f is not None else None
    atr = _dec(f.atr_14) if f is not None else None
    atr_pct_rank = _dec(f.atr_percentile_100) if f is not None else None
    ema_21 = _dec(f.ema_21) if f is not None else None
    ema_55 = _dec(f.ema_55) if f is not None else None
    donchian_upper = _dec(f.donchian_upper) if f is not None else None
    donchian_lower = _dec(f.donchian_lower) if f is not None else None

    if atr is None or atr <= Decimal("0"):
        atr = close * Decimal("0.01")
    atr_pct = (atr / close) if close > Decimal("0") else Decimal("0.01")

    return {
        "mid_price": close,
        "now": datetime.now(timezone.utc),
        "reference_price": reference_price if reference_price is not None else close,
        "rsi": rsi if rsi is not None else Decimal("50"),
        "bb_lower": bb_lower if bb_lower is not None else close * Decimal("0.98"),
        "bb_upper": bb_upper if bb_upper is not None else close * Decimal("1.02"),
        "atr": atr,
        "atr_pct": atr_pct,
        "atr_percentile": atr_pct_rank if atr_pct_rank is not None else Decimal("50"),
        "ma_fast": ema_21 if ema_21 is not None else close,
        "ma_slow": ema_55 if ema_55 is not None else close,
        "ma_n": ema_55 if ema_55 is not None else close,
        "donchian_high": (
            donchian_upper if donchian_upper is not None
            else (window_high if window_high is not None else close)
        ),
        "donchian_low": (
            donchian_lower if donchian_lower is not None
            else (window_low if window_low is not None else close)
        ),
    }
