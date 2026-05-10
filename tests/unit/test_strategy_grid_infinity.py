"""Phase 3 Wave 1 Task 2.2 — A2 Infinity Grid unit tests.

A2 differs from A1 (Standard Grid) in two ways:

  1. Geometric rung spacing instead of arithmetic. Rung i is at
     low * (1 + step_pct) ** i.
  2. The grid is *not* capped at a fixed `high`. When mid_price climbs
     to within one step of the current top rung, the strategy spawns
     a new top rung at top * (1 + step_pct). This is what "captures
     uptrend drift" means in spec §2.1.

Buy-ladder deploy and sell-against-fill semantics are otherwise the
same as A1: every rung at-or-below mid is a buy at first tick;
when state['levels'][i]['filled_buy']=True (worker-delivered),
the next tick emits a sell at rung i+1.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.grid.infinity import InfinityGridStrategy


def _ctx(
    *,
    strategy_id: int = 202,
    low: float = 100,
    step_pct: float = 0.02,
    levels: int = 5,
    capital_usd: float = 50,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="grid_infinity",
        symbol="BTCUSDT",
        params={
            "low": str(low),
            "step_pct": str(step_pct),
            "levels": levels,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First-tick deployment of the buy ladder ----------


def test_first_tick_geometric_ladder_below_mid():
    """5 levels, low=100, step=0.02 → prices [100, 102, 104.04, 106.1208,
    108.243216]. mid=105 → 3 rungs <= mid (100, 102, 104.04)."""
    s = InfinityGridStrategy()
    ctx = _ctx(low=100, step_pct=0.02, levels=5, capital_usd=50)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    assert len(intents) == 3
    for it in intents:
        assert isinstance(it, OrderIntent)
        assert it.side == "long"
        assert it.order_type == "limit"
        assert it.role == "entry"
        assert it.size_usd == Decimal("10")  # 50 / 5 levels
    prices = sorted(it.limit_price for it in intents)
    # Geometric spacing
    assert prices[0] == Decimal("100")
    assert prices[1] == Decimal("102.00")
    assert prices[2] == Decimal("104.0400")


def test_first_tick_persists_ladder_with_full_per_rung_schema():
    """State after first tick: 5 levels, each with the A1-style schema
    plus one A2 addition — `step_pct` is recorded at the top level so
    the strategy can re-derive new rungs deterministically."""
    s = InfinityGridStrategy()
    ctx = _ctx(low=100, step_pct=0.02, levels=5, capital_usd=50)

    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    assert ctx.state["step_pct"] == "0.02"
    assert len(ctx.state["levels"]) == 5
    for lv in ctx.state["levels"]:
        assert lv["side"] == "buy"
        assert lv["filled_buy"] is False
        assert lv["submitted_sell"] is False
        assert "price" in lv
        assert "client_order_id" in lv
    submitted = [lv["submitted"] for lv in ctx.state["levels"]]
    assert submitted == [True, True, True, False, False]


def test_first_tick_client_order_ids():
    s = InfinityGridStrategy()
    ctx = _ctx(strategy_id=99, low=100, step_pct=0.02, levels=3,
               capital_usd=30)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("110")})

    coids = sorted(it.client_order_id for it in intents)
    assert coids == [
        "gridinf-99-L0-entry",
        "gridinf-99-L1-entry",
        "gridinf-99-L2-entry",
    ]


# ---------- Sell-against-fill (parity with A1) ----------


def _seeded_state(*, mid: Decimal = Decimal("105")) -> StrategyContext:
    s = InfinityGridStrategy()
    ctx = _ctx(low=100, step_pct=0.02, levels=5, capital_usd=50)
    s.tick(ctx, snapshot={"mid_price": mid})
    return ctx


def test_filled_buy_emits_sell_at_next_higher_rung():
    s = InfinityGridStrategy()
    ctx = _seeded_state()

    ctx.state["levels"][0]["filled_buy"] = True
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    sells = [it for it in intents if it.role == "exit"]
    assert len(sells) == 1
    sell = sells[0]
    assert sell.limit_price == Decimal("102.00")
    assert sell.size_usd == Decimal("10")
    assert sell.client_order_id == "gridinf-202-L1-exit"
    assert sell.grid_level == 1
    assert ctx.state["levels"][0]["submitted_sell"] is True


def test_already_submitted_sell_is_not_re_emitted():
    s = InfinityGridStrategy()
    ctx = _seeded_state()

    ctx.state["levels"][0]["filled_buy"] = True
    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("105")})
    sells = [it for it in intents if it.role == "exit"]
    assert sells == []


# ---------- Infinity expansion (the A2 differentiator) ----------


def test_mid_within_one_step_of_top_spawns_new_top_rung():
    """Top rung is at 108.243216. step=0.02 → expansion threshold is
    top * (1 - step) = 108.243216 * 0.98 = 106.07835168. When mid >=
    that threshold AND price hasn't already exceeded the top, spawn
    one new top rung at top * (1 + step) = 108.243216 * 1.02 = ~110.408."""
    s = InfinityGridStrategy()
    ctx = _seeded_state(mid=Decimal("105"))
    initial_top = Decimal(ctx.state["levels"][-1]["price"])
    initial_count = len(ctx.state["levels"])

    # Climb mid up to within one step of the top rung.
    s.tick(ctx, snapshot={"mid_price": Decimal("107")})

    assert len(ctx.state["levels"]) == initial_count + 1
    new_top = Decimal(ctx.state["levels"][-1]["price"])
    expected = initial_top * (Decimal("1") + Decimal("0.02"))
    assert new_top == expected
    assert ctx.state["levels"][-1]["submitted"] is False
    assert ctx.state["levels"][-1]["filled_buy"] is False


def test_mid_far_below_top_does_not_spawn():
    """When mid is comfortably below the top rung, no expansion."""
    s = InfinityGridStrategy()
    ctx = _seeded_state(mid=Decimal("105"))
    initial_count = len(ctx.state["levels"])

    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    assert len(ctx.state["levels"]) == initial_count


def test_repeated_climbs_spawn_multiple_new_rungs_one_per_tick():
    """Each tick spawns at most one new rung. Two ticks at climbing
    mid → two new rungs (one per tick)."""
    s = InfinityGridStrategy()
    ctx = _seeded_state(mid=Decimal("105"))
    initial_count = len(ctx.state["levels"])

    s.tick(ctx, snapshot={"mid_price": Decimal("107")})
    assert len(ctx.state["levels"]) == initial_count + 1

    # Now top is at ~110.408. (1 - 0.02) * 110.408 = ~108.2 → mid 109
    # crosses the new threshold.
    s.tick(ctx, snapshot={"mid_price": Decimal("109")})
    assert len(ctx.state["levels"]) == initial_count + 2


# ---------- Param validation ----------


def test_levels_below_two_raises():
    s = InfinityGridStrategy()
    ctx = _ctx(low=100, step_pct=0.02, levels=1)
    with pytest.raises(ValueError, match="levels"):
        s.tick(ctx, snapshot={"mid_price": Decimal("105")})


def test_step_pct_must_be_positive():
    s = InfinityGridStrategy()
    ctx = _ctx(low=100, step_pct=0.0, levels=5)
    with pytest.raises(ValueError, match="step_pct"):
        s.tick(ctx, snapshot={"mid_price": Decimal("105")})


def test_low_must_be_positive():
    s = InfinityGridStrategy()
    ctx = _ctx(low=0, step_pct=0.02, levels=5)
    with pytest.raises(ValueError, match="low"):
        s.tick(ctx, snapshot={"mid_price": Decimal("105")})


def test_missing_mid_price_raises():
    s = InfinityGridStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = InfinityGridStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = InfinityGridStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_trend_up_and_range_volatile():
    """A2 spec §2.1 best regime: RANGE_VOLATILE + slight TREND_UP.
    Compat map (§6.2): [RANGE_VOLATILE, TREND_UP]. Expected returns
    must reflect that ordering — both regimes positive, others zero."""
    s = InfinityGridStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
