"""Phase 0 gating: threshold + per-(symbol,archetype) cooldown.

State is in-memory for Phase 0 unit purposes. In production the signal worker
uses Postgres to look up the last fired_at for (symbol, archetype) — implemented
in Task 18's worker integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from trading_sandwich.contracts.models import Signal


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
