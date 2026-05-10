"""Phase 3 Wave 1 Task 2.1 — A1 Standard Grid unit tests.

Pins the minimal A1 contract:

  Params: low (Decimal/number), high (Decimal/number), levels (int >=2).
  Mechanism: evenly-spaced price ladder between low and high. On first
    tick, emit a buy LIMIT at every grid level <= mid_price. Sells are
    only emitted on subsequent ticks once buy fills are observed (fill
    plumbing arrives in a later Wave 1 task — A1's first cut emits the
    initial buy ladder only).
  Idempotent: with the same persisted state, a re-tick emits no
    additional intents (the ladder is already submitted).
  Halal-spot: every emitted intent has side='long', role='entry'.

OrderIntent.client_order_id format: 'gridstd-{strategy_id}-L{level}-{role}'.
The level index pins each rung deterministically across ticks; the role
suffix lets us tell entry-buys from later exit-sells when fills arrive.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.grid.standard import StandardGridStrategy


def _ctx(
    *,
    strategy_id: int = 101,
    low: float = 60_000,
    high: float = 70_000,
    levels: int = 5,
    capital_usd: float = 30,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="grid_standard",
        symbol="BTCUSDT",
        params={
            "low": str(low),
            "high": str(high),
            "levels": levels,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First-tick deployment of the buy ladder ----------


def test_first_tick_emits_buy_ladder_below_mid():
    """5-level grid 60k-70k with mid=65k: levels are at 60k, 62.5k, 65k,
    67.5k, 70k. Buys go on every level <= mid (60k, 62.5k, 65k) → 3 buys."""
    s = StandardGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    assert len(intents) == 3
    for it in intents:
        assert isinstance(it, OrderIntent)
        assert it.side == "long"
        assert it.order_type == "limit"
        assert it.role == "entry"
        assert it.symbol == "BTCUSDT"
        assert it.size_usd == Decimal("6")  # 30 / 5 levels
    prices = sorted(it.limit_price for it in intents)
    assert prices == [Decimal("60000"), Decimal("62500"), Decimal("65000")]


def test_first_tick_client_order_ids_pin_level_index():
    """client_order_id encodes strategy_id + level index + role so we can
    reconcile fills back to a specific rung."""
    s = StandardGridStrategy()
    ctx = _ctx(strategy_id=42, low=100, high=200, levels=3)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("200")})

    # All 3 levels (100, 150, 200) are <= mid → 3 buys, level indices 0,1,2.
    coids = sorted(it.client_order_id for it in intents)
    assert coids == [
        "gridstd-42-L0-entry",
        "gridstd-42-L1-entry",
        "gridstd-42-L2-entry",
    ]
    grid_levels = sorted(it.grid_level for it in intents)
    assert grid_levels == [0, 1, 2]


def test_first_tick_persists_ladder_to_state():
    """After the first tick, ctx.state['levels'] holds one entry per
    grid level with submitted=True for the buys we emitted."""
    s = StandardGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5)

    s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    assert "levels" in ctx.state
    levels = ctx.state["levels"]
    assert len(levels) == 5
    # Sorted by price ascending
    prices = [Decimal(level["price"]) for level in levels]
    assert prices == [
        Decimal("60000"), Decimal("62500"), Decimal("65000"),
        Decimal("67500"), Decimal("70000"),
    ]
    submitted = [level["submitted"] for level in levels]
    # First three (<=mid) submitted; top two not yet.
    assert submitted == [True, True, True, False, False]
    sides = [level["side"] for level in levels]
    assert sides == ["buy", "buy", "buy", "buy", "buy"]


# ---------- Idempotency ----------


def test_re_tick_with_existing_state_emits_no_intents():
    """The second tick with the same state must produce zero new intents
    — the ladder is already deployed. (Fill-driven re-placement comes
    in a later Wave 1 supporting task once execution plumbing lands.)"""
    s = StandardGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5)

    first = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    assert len(first) == 3

    second = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    assert second == []


def test_re_tick_does_not_mutate_state():
    """Idempotency includes state — a no-op tick must not rewrite the
    ladder (otherwise optimistic-lock churn on every tick)."""
    s = StandardGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5)

    s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    snapshot_before = [dict(lv) for lv in ctx.state["levels"]]

    s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    snapshot_after = [dict(lv) for lv in ctx.state["levels"]]
    assert snapshot_after == snapshot_before


# ---------- Param validation ----------


def test_levels_below_two_raises():
    s = StandardGridStrategy()
    ctx = _ctx(low=100, high=200, levels=1)
    with pytest.raises(ValueError, match="levels"):
        s.tick(ctx, snapshot={"mid_price": Decimal("150")})


def test_low_must_be_below_high():
    s = StandardGridStrategy()
    ctx = _ctx(low=200, high=100, levels=5)
    with pytest.raises(ValueError, match="low"):
        s.tick(ctx, snapshot={"mid_price": Decimal("150")})


def test_missing_required_param_raises():
    s = StandardGridStrategy()
    ctx = StrategyContext(
        strategy_id=1,
        strategy_type="grid_standard",
        symbol="BTCUSDT",
        params={"low": "100"},  # missing high, levels
        state={},
        capital_allocated_usd=Decimal("30"),
    )
    with pytest.raises((KeyError, ValueError)):
        s.tick(ctx, snapshot={"mid_price": Decimal("150")})


def test_missing_mid_price_in_snapshot_raises():
    s = StandardGridStrategy()
    ctx = _ctx()
    with pytest.raises((KeyError, ValueError)):
        s.tick(ctx, snapshot={})


# ---------- Mid above the entire ladder: all buys submit ----------


def test_mid_above_high_emits_all_levels_as_buys():
    """If price has run above the grid range entirely, the buy ladder
    is fully under price — every level is a buy and all are submitted."""
    s = StandardGridStrategy()
    ctx = _ctx(low=100, high=200, levels=5)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("250")})

    assert len(intents) == 5
    submitted = [lv["submitted"] for lv in ctx.state["levels"]]
    assert submitted == [True] * 5


def test_mid_below_low_emits_no_intents_but_records_ladder():
    """If price is below the entire grid, no buys yet (we'd be buying
    above market). State still records the ladder so a later tick when
    price re-enters the range can submit. No intents this tick."""
    s = StandardGridStrategy()
    ctx = _ctx(low=100, high=200, levels=5)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("50")})

    assert intents == []
    assert len(ctx.state["levels"]) == 5
    submitted = [lv["submitted"] for lv in ctx.state["levels"]]
    assert submitted == [False] * 5


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = StandardGridStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = StandardGridStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_highest_in_range_volatile():
    """A1 Standard Grid is a range-capture strategy — its expected
    monthly return is highest in RANGE_VOLATILE, smaller in RANGE_QUIET
    and TREND_UP, and zero in regimes where it shouldn't run."""
    s = StandardGridStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > rq.monthly_return_pct
    assert rv.monthly_return_pct > tu.monthly_return_pct
    assert td.monthly_return_pct == Decimal("0")
    assert 0.0 <= rv.confidence <= 1.0
