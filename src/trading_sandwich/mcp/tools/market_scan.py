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
    """Stub for v1. Real impl in Spec B calls tradingview MCP scanners."""
    return []


@mcp.tool()
async def get_top_movers(window: str = "24h", limit: int = 10) -> list[dict]:
    """Return symbols with largest price changes in the given window."""
    return await _fetch_top_movers(window, limit)
