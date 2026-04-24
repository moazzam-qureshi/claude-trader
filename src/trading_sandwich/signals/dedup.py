"""Dedup gate: strictly-higher-timeframe signal for the same (symbol, direction)
within the dedup window suppresses the current candidate.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from trading_sandwich._async import run_coro
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM

_TIMEFRAME_RANK = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "1d": 4}


def _higher_timeframes(timeframe: str) -> list[str]:
    rank = _TIMEFRAME_RANK.get(timeframe, -1)
    return [tf for tf, r in _TIMEFRAME_RANK.items() if r > rank]


async def _check_async(
    symbol: str, direction: str, timeframe: str,
    fired_at: datetime, window_minutes: int,
) -> bool:
    session_factory = get_session_factory()
    higher = _higher_timeframes(timeframe)
    if not higher:
        return False
    cutoff = fired_at - timedelta(minutes=window_minutes)
    async with session_factory() as session:
        hit = (await session.execute(
            select(SignalORM.signal_id)
            .where(
                SignalORM.symbol == symbol,
                SignalORM.direction == direction,
                SignalORM.gating_outcome == "claude_triaged",
                SignalORM.timeframe.in_(higher),
                SignalORM.fired_at >= cutoff,
                SignalORM.fired_at <= fired_at,
            )
            .limit(1)
        )).scalar_one_or_none()
    return hit is not None


def is_dedup_suppressed(
    *,
    symbol: str, direction: str, timeframe: str,
    fired_at: datetime, window_minutes: int,
) -> bool:
    return run_coro(_check_async(symbol, direction, timeframe, fired_at, window_minutes))
