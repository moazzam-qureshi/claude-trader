"""MCP tools for market scanning: get_recent_signals, get_top_movers."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.mcp.server import mcp


_SINCE_RE = re.compile(r"^(\d+)([hmd])$")


def _parse_since(since: str) -> timedelta:
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"invalid since: {since}")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


@mcp.tool()
async def get_recent_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    since: str = "24h",
    limit: int = 50,
) -> list[dict]:
    """Query recent signals fired by the rule pipeline."""
    cutoff = datetime.now(timezone.utc) - _parse_since(since)
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(SignalORM)
            .where(SignalORM.fired_at >= cutoff)
            .order_by(SignalORM.fired_at.desc())
            .limit(limit)
        )
        if symbol:
            stmt = stmt.where(SignalORM.symbol == symbol)
        if timeframe:
            stmt = stmt.where(SignalORM.timeframe == timeframe)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "signal_id": str(r.signal_id),
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "archetype": r.archetype,
                "direction": r.direction,
                "fired_at": r.fired_at.isoformat(),
                "trigger_price": float(r.trigger_price) if r.trigger_price else None,
                "confidence": float(r.confidence) if r.confidence else None,
                "gating_outcome": r.gating_outcome,
            }
            for r in rows
        ]


async def _fetch_top_movers(window: str, limit: int) -> list[dict]:
    """v1.1: query Binance public ticker24h endpoint for top USDT movers.

    Returns symbols sorted by absolute % change. No auth required.
    Spec B may replace with TradingView scanner integration.
    """
    import httpx
    base = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(base)
            data = resp.json()
    except Exception as exc:
        return [{"error": f"fetch failed: {type(exc).__name__}: {exc}"}]

    usdt = [t for t in data if t.get("symbol", "").endswith("USDT")]
    for t in usdt:
        try:
            t["_abspct"] = abs(float(t.get("priceChangePercent", "0") or 0))
        except (TypeError, ValueError):
            t["_abspct"] = 0.0
    usdt.sort(key=lambda t: t["_abspct"], reverse=True)
    out = []
    for t in usdt[:limit]:
        try:
            out.append({
                "symbol": t["symbol"],
                "change_pct": float(t.get("priceChangePercent", 0) or 0),
                "last_price": float(t.get("lastPrice", 0) or 0),
                "volume_usd": float(t.get("quoteVolume", 0) or 0),
            })
        except (TypeError, ValueError, KeyError):
            continue
    return out


@mcp.tool()
async def get_top_movers(window: str = "24h", limit: int = 10) -> list[dict]:
    """Return USDT-pair symbols with largest abs % price change in window.

    v1.1 supports window='24h' only (Binance ticker24h endpoint).
    Returns: [{symbol, change_pct, last_price, volume_usd}, ...] sorted by
    abs(change_pct) descending. Use to spot symbols outside your universe
    that are moving — discovery, not signal.
    """
    return await _fetch_top_movers(window, limit)


@mcp.tool()
async def get_pipeline_health() -> dict:
    """Snapshot of the data pipeline's recent activity.

    Use to verify the rule pipeline is alive before concluding
    'no signals fired = market quiet' (vs. 'pipeline dead'). Returns
    row counts in the last 1h window for raw_candles, features,
    signals, and heartbeat_shifts.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, select
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import (
        Features, RawCandle, Signal, SignalOutcome,
    )
    from trading_sandwich.db.models_heartbeat import HeartbeatShift

    factory = get_session_factory()
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)

    async with factory() as session:
        candles_1h = (await session.execute(
            select(func.count(RawCandle.symbol)).where(
                RawCandle.close_time >= one_hour_ago,
            )
        )).scalar_one()
        features_1h = (await session.execute(
            select(func.count()).select_from(Features).where(
                Features.close_time >= one_hour_ago,
            )
        )).scalar_one()
        signals_24h = (await session.execute(
            select(func.count(Signal.signal_id)).where(
                Signal.fired_at >= one_day_ago,
            )
        )).scalar_one()
        signals_1h = (await session.execute(
            select(func.count(Signal.signal_id)).where(
                Signal.fired_at >= one_hour_ago,
            )
        )).scalar_one()
        outcomes_24h = (await session.execute(
            select(func.count()).select_from(SignalOutcome).where(
                SignalOutcome.measured_at >= one_day_ago,
            )
        )).scalar_one()
        shifts_24h = (await session.execute(
            select(func.count(HeartbeatShift.id)).where(
                HeartbeatShift.started_at >= one_day_ago,
            )
        )).scalar_one()

    return {
        "raw_candles_1h": candles_1h,
        "features_1h": features_1h,
        "signals_1h": signals_1h,
        "signals_24h": signals_24h,
        "outcomes_24h": outcomes_24h,
        "heartbeat_shifts_24h": shifts_24h,
        "pipeline_alive": candles_1h > 0 or features_1h > 0,
    }
