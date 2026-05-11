"""Strategy ABC + state machine — Phase 3 plan Task 1.6.

Every mechanical strategy in Phase 3 is an executor that:

    pending -> active -> paused -> winding_down -> completed
                          ^         |
                          +---------+   (resume)
    any -> errored                      (terminal failure)

Strategies are STATELESS workers. The strategy-worker (Task 1.15) loads
the persisted state, builds a StrategyContext, calls tick(), persists
whatever state the strategy mutated. The strategy itself holds no
in-memory state across ticks — that's what makes them crash-safe.

The DB CHECK constraint on strategies.status (migration 0013) pins the
set of legal status strings; StrategyStatus enum values must match
exactly. test_strategy_base::test_status_strings_match_db_check_constraint
guards this.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrategyStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    WINDING_DOWN = "winding_down"
    COMPLETED = "completed"
    ERRORED = "errored"


class Regime(str, enum.Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE_VOLATILE = "range_volatile"
    RANGE_QUIET = "range_quiet"
    TRANSITIONING = "transitioning"


class InvalidTransitionError(Exception):
    """Raised when a state-machine action is illegal from the current state."""


# action -> (legal_from_set, target)
_TRANSITIONS: dict[str, tuple[set[StrategyStatus], StrategyStatus]] = {
    "deploy": ({StrategyStatus.PENDING}, StrategyStatus.ACTIVE),
    "pause": ({StrategyStatus.ACTIVE}, StrategyStatus.PAUSED),
    "resume": ({StrategyStatus.PAUSED}, StrategyStatus.ACTIVE),
    "wind_down": (
        {StrategyStatus.ACTIVE, StrategyStatus.PAUSED},
        StrategyStatus.WINDING_DOWN,
    ),
    "complete": ({StrategyStatus.WINDING_DOWN}, StrategyStatus.COMPLETED),
    # 'error' is special: legal from every non-terminal state.
    "error": (
        {
            StrategyStatus.PENDING,
            StrategyStatus.ACTIVE,
            StrategyStatus.PAUSED,
            StrategyStatus.WINDING_DOWN,
        },
        StrategyStatus.ERRORED,
    ),
}


def next_status(current: StrategyStatus, action: str) -> StrategyStatus:
    """Compute the next status after `action`. Raises
    InvalidTransitionError if the action is illegal from `current`."""
    rule = _TRANSITIONS.get(action)
    if rule is None:
        raise InvalidTransitionError(f"unknown action: {action!r}")
    legal_from, target = rule
    if current not in legal_from:
        raise InvalidTransitionError(
            f"cannot {action} from {current.value}"
            f" (legal from: {sorted(s.value for s in legal_from)})"
        )
    return target


# ---------- Strategy I/O contracts ----------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OrderIntent(_Frozen):
    """A strategy's request to place one order. The strategy-worker
    converts this into an OrderRequest before handing to the execution
    rail. Halal spot: side is long-only, enforced here.

    `side` (position side) is always 'long' — halal-spot inviolable.
    `direction` is the *trade* direction: 'buy' adds to the long, 'sell'
    reduces it. A 'sell' only ever liquidates inventory the strategy
    already holds — it never opens a short. role='exit'/'stop_loss'/
    'take_profit' are always sells; role='entry' is always a buy;
    role='rebalance' can be either (the rebalance family up- or down-
    sizes), which is exactly why `direction` exists — `role` alone is
    ambiguous there. Defaults to 'buy' so existing buy-emitting code is
    unchanged; sell-emitting branches set direction='sell' explicitly.
    """

    symbol: str
    side: Literal["long"] = "long"
    direction: Literal["buy", "sell"] = "buy"
    order_type: Literal["market", "limit", "stop"]
    size_usd: Decimal = Field(gt=Decimal("0"))
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    client_order_id: str = Field(min_length=1)
    role: Literal["entry", "exit", "rebalance", "stop_loss", "take_profit"]
    grid_level: int | None = None


class ReturnExpectation(_Frozen):
    """What a strategy expects to return per month in a given regime.
    Used by the performance tracker (Task 1.10) to flag underperformers."""

    monthly_return_pct: Decimal
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None


@dataclass
class StrategyContext:
    """Per-tick context handed to the strategy by the worker. The strategy
    reads from this and MAY mutate `state` to be persisted on return.
    Everything else is read-only."""

    strategy_id: int
    strategy_type: str
    symbol: str
    params: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)
    capital_allocated_usd: Decimal = Decimal("0")
    capital_deployed_usd: Decimal = Decimal("0")


# ---------- The ABC ----------


class Strategy(ABC):
    """Abstract base for every mechanical strategy.

    Subclass and implement all four methods. The worker calls tick()
    every cycle while status is ACTIVE; calls graceful_shutdown() on
    a wind_down command (cancel pending orders, keep filled positions);
    calls emergency_stop() on the kill switch (cancel + market-flatten
    if directed); and calls expected_return_for_regime() during
    performance evaluation.
    """

    @abstractmethod
    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        """Compute orders to place this cycle. Idempotent — the same
        snapshot + state must produce the same intents."""

    @abstractmethod
    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        """Cancel pending orders, keep filled positions, prepare for
        handoff. Called on `wind_down` action."""

    @abstractmethod
    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        """Cancel everything, optionally market-flatten positions.
        Called by the kill switch / circuit breaker."""

    @abstractmethod
    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        """What this strategy expects to earn per month in this regime.
        Used by the performance tracker to detect underperformance."""
