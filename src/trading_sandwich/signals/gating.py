"""Phase 0 in-memory gating (threshold + cooldown) — retained for unit tests.
Phase 1 adds `gate_signal_with_db`, the three-stage gate used by the signal
worker against Postgres.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from trading_sandwich._async import run_coro
from trading_sandwich._policy import (
    get_confidence_threshold,
    get_cooldown_minutes,
    get_dedup_window_minutes,
)
from trading_sandwich.contracts.models import Signal
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.signals.dedup import is_dedup_suppressed


@dataclass
class GatingState:
    last_fired: dict[tuple[str, str], datetime] = field(default_factory=dict)


def apply_gating(signal: Signal, state: GatingState, policy: dict) -> Signal:
    threshold = Decimal(str(policy["per_archetype_confidence_threshold"][signal.archetype]))
    if signal.confidence < threshold:
        return signal.model_copy(update={"gating_outcome": "below_threshold"})

    cooldown_min = policy["per_archetype_cooldown_minutes"][signal.archetype]
    key = (signal.symbol, signal.archetype)
    last = state.last_fired.get(key)
    if last is not None and signal.fired_at - last < timedelta(minutes=cooldown_min):
        return signal.model_copy(update={"gating_outcome": "cooldown_suppressed"})

    state.last_fired[key] = signal.fired_at
    return signal.model_copy(update={"gating_outcome": "claude_triaged"})


async def _cooldown_violated_async(signal: Signal) -> bool:
    cooldown_min = get_cooldown_minutes(signal.archetype)
    cutoff = signal.fired_at - timedelta(minutes=cooldown_min)
    session_factory = get_session_factory()
    async with session_factory() as session:
        last = (await session.execute(
            select(SignalORM.fired_at)
            .where(
                SignalORM.symbol == signal.symbol,
                SignalORM.archetype == signal.archetype,
                SignalORM.gating_outcome == "claude_triaged",
                SignalORM.fired_at >= cutoff,
                SignalORM.fired_at <= signal.fired_at,
            )
            .order_by(SignalORM.fired_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    return last is not None


def gate_signal_with_db(signal: Signal) -> Signal:
    """Three-stage gate applied strictly in order:
       1. below_threshold
       2. cooldown_suppressed
       3. dedup_suppressed
    First non-pass stage short-circuits.
    """
    threshold = get_confidence_threshold(signal.archetype)
    if signal.confidence < threshold:
        return signal.model_copy(update={"gating_outcome": "below_threshold"})

    if run_coro(_cooldown_violated_async(signal)):
        return signal.model_copy(update={"gating_outcome": "cooldown_suppressed"})

    window = get_dedup_window_minutes()
    if is_dedup_suppressed(
        symbol=signal.symbol, direction=signal.direction,
        timeframe=signal.timeframe, fired_at=signal.fired_at,
        window_minutes=window,
    ):
        return signal.model_copy(update={"gating_outcome": "dedup_suppressed"})

    return signal.model_copy(update={"gating_outcome": "claude_triaged"})
