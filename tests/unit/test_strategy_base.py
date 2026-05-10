"""Phase 3 plan Task 1.6 — Strategy ABC + state machine.

Pins the strategy lifecycle contract:
  pending -> active -> paused -> winding_down -> completed
                        ^         |
                        +---------+    (resume)
  any -> errored                       (terminal failure)

The ABC enforces four methods:
  tick(snapshot) -> list[OrderIntent]
  graceful_shutdown() -> list[OrderIntent]
  emergency_stop() -> list[OrderIntent]
  expected_return_for_regime(regime) -> ReturnExpectation

Strategies are stateless — every tick reads state from the caller's
StrategyContext (id, params, persisted state dict), computes intents,
and returns. State writes happen in the worker, not the strategy.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    InvalidTransitionError,
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
    StrategyStatus,
    next_status,
)


# ---------- ABC enforcement ----------

class _Incomplete(Strategy):
    """Subclass that fails to override the abstract methods. Used to
    confirm Strategy() cannot be instantiated when methods are missing."""


class _Concrete(Strategy):
    def tick(self, ctx: StrategyContext, snapshot: dict) -> list[OrderIntent]:
        return []

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        return ReturnExpectation(monthly_return_pct=Decimal("0.05"), confidence=0.5)


def test_cannot_instantiate_strategy_without_overrides():
    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_concrete_strategy_instantiates_and_returns_empty_intents():
    s = _Concrete()
    ctx = StrategyContext(
        strategy_id=42,
        strategy_type="grid_standard",
        symbol="BTCUSDT",
        params={"low": 60000, "high": 70000, "levels": 5},
        state={},
    )
    assert s.tick(ctx, {}) == []
    assert s.graceful_shutdown(ctx) == []
    assert s.emergency_stop(ctx) == []
    er = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    assert er.confidence == 0.5


# ---------- State machine ----------

def test_pending_to_active_allowed():
    assert next_status(StrategyStatus.PENDING, "deploy") == StrategyStatus.ACTIVE


def test_active_to_paused_allowed():
    assert next_status(StrategyStatus.ACTIVE, "pause") == StrategyStatus.PAUSED


def test_paused_to_active_allowed():
    assert next_status(StrategyStatus.PAUSED, "resume") == StrategyStatus.ACTIVE


def test_active_to_winding_down_allowed():
    assert next_status(StrategyStatus.ACTIVE, "wind_down") == StrategyStatus.WINDING_DOWN


def test_paused_to_winding_down_allowed():
    """A paused strategy can be wound down without resuming first."""
    assert next_status(StrategyStatus.PAUSED, "wind_down") == StrategyStatus.WINDING_DOWN


def test_winding_down_to_completed_allowed():
    assert (
        next_status(StrategyStatus.WINDING_DOWN, "complete")
        == StrategyStatus.COMPLETED
    )


def test_any_state_to_errored_allowed():
    """Errored is terminal-failure; reachable from any non-terminal state."""
    for src in (
        StrategyStatus.PENDING,
        StrategyStatus.ACTIVE,
        StrategyStatus.PAUSED,
        StrategyStatus.WINDING_DOWN,
    ):
        assert next_status(src, "error") == StrategyStatus.ERRORED


def test_completed_is_terminal():
    """Completed strategies cannot transition further."""
    with pytest.raises(InvalidTransitionError):
        next_status(StrategyStatus.COMPLETED, "resume")
    with pytest.raises(InvalidTransitionError):
        next_status(StrategyStatus.COMPLETED, "pause")


def test_errored_is_terminal():
    """Errored strategies cannot transition further (they get
    investigated and potentially redeployed as a NEW row)."""
    with pytest.raises(InvalidTransitionError):
        next_status(StrategyStatus.ERRORED, "resume")
    with pytest.raises(InvalidTransitionError):
        next_status(StrategyStatus.ERRORED, "wind_down")


def test_invalid_transitions_rejected():
    # pending cannot pause/resume/wind_down — must deploy first
    for action in ("pause", "resume", "wind_down", "complete"):
        with pytest.raises(InvalidTransitionError):
            next_status(StrategyStatus.PENDING, action)
    # active cannot deploy again, cannot complete directly (must wind_down first)
    with pytest.raises(InvalidTransitionError):
        next_status(StrategyStatus.ACTIVE, "deploy")
    with pytest.raises(InvalidTransitionError):
        next_status(StrategyStatus.ACTIVE, "complete")
    # winding_down cannot pause/resume/deploy
    for action in ("pause", "resume", "deploy"):
        with pytest.raises(InvalidTransitionError):
            next_status(StrategyStatus.WINDING_DOWN, action)


def test_status_strings_match_db_check_constraint():
    """Migration 0013 ck_strategies_status_valid pins the set of legal
    status strings. The Python enum values must match exactly."""
    expected = {"pending", "active", "paused", "winding_down", "completed", "errored"}
    actual = {s.value for s in StrategyStatus}
    assert actual == expected


# ---------- OrderIntent contract ----------

def test_order_intent_is_immutable_and_typed():
    intent = OrderIntent(
        symbol="BTCUSDT",
        side="long",
        order_type="limit",
        size_usd=Decimal("25.50"),
        limit_price=Decimal("65000.00"),
        client_order_id="grid-42-l3-buy-001",
        role="entry",
    )
    # Immutable (pydantic frozen)
    with pytest.raises(Exception):
        intent.symbol = "ETHUSDT"  # type: ignore[misc]
    # Required fields present
    assert intent.size_usd == Decimal("25.50")
    assert intent.role == "entry"


def test_order_intent_rejects_short_side():
    """Halal spot — longs only. The base contract pins this so no
    strategy can accidentally emit a short intent."""
    with pytest.raises(Exception):
        OrderIntent(
            symbol="BTCUSDT",
            side="short",  # type: ignore[arg-type]
            order_type="market",
            size_usd=Decimal("10"),
            client_order_id="bad-001",
            role="entry",
        )


def test_return_expectation_has_required_fields():
    er = ReturnExpectation(
        monthly_return_pct=Decimal("0.04"),
        confidence=0.7,
        rationale="grid in range_volatile historically captures ~4%/mo",
    )
    assert er.monthly_return_pct == Decimal("0.04")
    assert 0 <= er.confidence <= 1
    assert "grid" in (er.rationale or "")
