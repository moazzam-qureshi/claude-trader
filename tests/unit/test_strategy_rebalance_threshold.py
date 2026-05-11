"""Phase 3 Wave 1 Task 2.15 — C2 Threshold Rebalancing unit tests.

Same single-symbol gap-close as C1, but the trigger is drift magnitude
rather than a calendar interval:

  target_value = target_fraction * capital_allocated_usd
  actual_value = position_units * mid
  drift = |actual_value - target_value| / target_value
  drift > drift_threshold (default 0.15 — Shrimpy's sweet spot)
    → rebalance to target (buy or sell, capped)
  else → no-op

First tick: empty position → drift = 1.0 > threshold → establishes
the target position.

Halal-spot inviolable: every emitted intent has side='long'. Sell
value capped at the held value — never goes short.

Snapshot contract: {'mid_price': Decimal}. (No 'now' — purely
drift-driven.) State: position_units, rebalance_count.

Spec §6.2 compat: ["*"] — universal.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.rebalance.threshold import (
    ThresholdRebalanceStrategy,
)


def _ctx(
    *,
    strategy_id: int = 1515,
    target_fraction: float = 0.5,
    drift_threshold: float = 0.15,
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="rebalance_threshold",
        symbol="BTCUSDT",
        params={
            "target_fraction": str(target_fraction),
            "drift_threshold": str(drift_threshold),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick establishes the position ----------


def test_first_tick_establishes_target_position():
    """Empty position → 100% drift > 15% threshold → buy to target.
    target = 0.5*1000 = 500 worth at mid 50000."""
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("50000")})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("500")
    assert it.client_order_id.startswith("rebthr-1515-")
    assert Decimal(ctx.state["position_units"]) == Decimal("0.01")
    assert ctx.state["rebalance_count"] == 1


# ---------- Small drift → no rebalance ----------


def test_small_drift_below_threshold_is_noop():
    """0.01 units at 50000 = 500 = target. Price ticks to 52000 →
    actual = 520 → drift = 20/500 = 0.04 < 0.15 → no-op."""
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000, drift_threshold=0.15,
               state={"position_units": "0.01", "rebalance_count": 1})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("52000")})
    assert intents == []
    assert ctx.state["rebalance_count"] == 1
    # State still rewritten (position unchanged) — that's fine.
    assert Decimal(ctx.state["position_units"]) == Decimal("0.01")


def test_drift_exactly_at_threshold_is_noop():
    """Strict greater-than: drift == threshold → no rebalance.
    0.01 units, target 500. Need actual such that |actual-500|/500 ==
    0.15 → actual = 575 → mid = 57500."""
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000, drift_threshold=0.15,
               state={"position_units": "0.01", "rebalance_count": 1})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("57500")})
    assert intents == []


# ---------- Large drift up → sell back ----------


def test_large_upward_drift_sells_to_target():
    """0.01 units at 50000. Price spikes to 90000 → actual = 900 →
    drift = 400/500 = 0.8 > 0.15 → sell 400 worth (back to 500)."""
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000, drift_threshold=0.15,
               state={"position_units": "0.01", "rebalance_count": 1})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("90000")})

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.size_usd == Decimal("400")
    assert ctx.state["rebalance_count"] == 2
    assert Decimal(ctx.state["position_units"]) < Decimal("0.01")


# ---------- Large drift down → buy back ----------


def test_large_downward_drift_buys_to_target():
    """0.01 units at 50000. Price drops to 20000 → actual = 200 →
    drift = 300/500 = 0.6 > 0.15 → buy 300 worth (back to 500)."""
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000, drift_threshold=0.15,
               state={"position_units": "0.01", "rebalance_count": 1})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("20000")})

    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd == Decimal("300")
    assert ctx.state["rebalance_count"] == 2
    assert Decimal(ctx.state["position_units"]) > Decimal("0.01")


def test_sell_capped_at_position_value():
    s = ThresholdRebalanceStrategy()
    # Tiny position, target much smaller than actual — sell can't
    # exceed held value.
    ctx = _ctx(target_fraction=0.001, capital_usd=1000, drift_threshold=0.15,
               state={"position_units": "0.0001", "rebalance_count": 1})
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("100000")})
    # actual = 10, target = 1 → drift = 9 > 0.15 → sell 9 (within 10).
    assert len(intents) == 1
    assert intents[0].size_usd <= Decimal("10")
    assert Decimal(ctx.state["position_units"]) >= Decimal("0")


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = ThresholdRebalanceStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={})


def test_target_fraction_out_of_range_raises():
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(target_fraction=1.5)
    with pytest.raises(ValueError, match="target_fraction"):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


def test_nonpositive_drift_threshold_raises():
    s = ThresholdRebalanceStrategy()
    ctx = _ctx(drift_threshold=0.0)
    with pytest.raises(ValueError, match="drift_threshold"):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = ThresholdRebalanceStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = ThresholdRebalanceStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_chop():
    """Spec §6.2 compat: ["*"]. Threshold rebalancing only acts on big
    moves — best in choppy regimes where drift swings wide. Positive
    everywhere."""
    s = ThresholdRebalanceStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    for r in (rv, rq, tu, td):
        assert r.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct > rq.monthly_return_pct
