"""Phase 3 Wave 1 Task 2.17 — C4 HODL++ (Grid + Rebalance) unit tests.

A composite: split allocated capital into a core leg and a grid leg.

  core leg : core_fraction * capital, periodically rebalanced to that
             target (like C1 Periodic Rebalancing)
  grid leg : (1 - core_fraction) * capital, run as a standard buy
             ladder between grid_low/grid_high (like A1 Standard Grid,
             sell-against-fill)

Both legs can emit intents in the same tick. State is nested:
  {"core": {position_units, rebalance_count, last_rebalance_at},
   "grid": {levels: [...]}}

First tick: deploys the grid ladder AND rebalances the core (which,
from a flat start, means a buy to the core target). Subsequent ticks:
core rebalances only after core_interval_seconds; grid emits sells for
filled buys.

Halal-spot inviolable: every emitted intent has side='long'. The grid
leg's sells reduce filled buys; the core leg's sells (if the core
appreciates above target) cap at the held core value — never short.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP].
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.hybrid.hodl_plus_plus import (
    HodlPlusPlusStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 1717,
    core_fraction: float = 0.7,
    core_interval_seconds: int = 2_592_000,
    grid_low: float = 40_000,
    grid_high: float = 60_000,
    grid_levels: int = 5,
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="hodl_plus_plus",
        symbol="BTCUSDT",
        params={
            "core_fraction": str(core_fraction),
            "core_interval_seconds": core_interval_seconds,
            "grid_low": str(grid_low),
            "grid_high": str(grid_high),
            "grid_levels": grid_levels,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick: grid ladder + core rebalance ----------


def test_first_tick_deploys_grid_and_rebalances_core():
    """capital 1000, core_fraction 0.7 → core target = 700; grid budget
    = 300 over 5 levels → 60 per level. mid 50000, grid 40k-60k → levels
    at 40k, 45k, 50k, 55k, 60k → buys at 40k, 45k, 50k (3 buys @60 each).
    Plus core rebalance: flat → buy 700 worth at mid."""
    s = HodlPlusPlusStrategy()
    ctx = _ctx(core_fraction=0.7, capital_usd=1000,
               grid_low=40_000, grid_high=60_000, grid_levels=5)

    intents = s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    # 3 grid entries + 1 core rebalance = 4 intents
    assert len(intents) == 4
    for it in intents:
        assert isinstance(it, OrderIntent)
        assert it.side == "long"
        assert it.order_type == "limit"

    grid_intents = [it for it in intents if it.role == "entry"]
    core_intents = [it for it in intents if it.role == "rebalance"]
    assert len(grid_intents) == 3
    assert len(core_intents) == 1
    assert all(it.size_usd == Decimal("60") for it in grid_intents)
    assert core_intents[0].size_usd == Decimal("700")
    assert core_intents[0].limit_price == Decimal("50000")

    # State nested
    assert "core" in ctx.state
    assert "grid" in ctx.state
    assert len(ctx.state["grid"]["levels"]) == 5
    assert ctx.state["core"]["rebalance_count"] == 1
    # 700 USD at 50000 → 0.014 units
    assert Decimal(ctx.state["core"]["position_units"]) == Decimal("0.014")


def test_grid_client_order_ids_namespaced():
    s = HodlPlusPlusStrategy()
    ctx = _ctx(strategy_id=88, grid_low=40_000, grid_high=60_000,
               grid_levels=3)

    intents = s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("60000")})
    grid_coids = sorted(
        it.client_order_id for it in intents if it.role == "entry"
    )
    # 3 levels (40k,50k,60k) all <= mid 60000 → 3 buys
    assert grid_coids == [
        "hodlpp-88-grid-L0-entry",
        "hodlpp-88-grid-L1-entry",
        "hodlpp-88-grid-L2-entry",
    ]
    core_coids = [
        it.client_order_id for it in intents if it.role == "rebalance"
    ]
    # rebalance_toward_value builds "{prefix}-{sid}-rb{seq}" with
    # prefix "hodlpp-core" → "hodlpp-core-88-rb0".
    assert core_coids[0] == "hodlpp-core-88-rb0"


# ---------- Re-tick: grid idempotent, core gated by interval ----------


def test_re_tick_within_core_interval_only_grid_logic():
    """Second tick, before the core interval: core does nothing; grid
    is already deployed → no new grid intents either."""
    s = HodlPlusPlusStrategy()
    ctx = _ctx(core_fraction=0.7, capital_usd=1000,
               grid_low=40_000, grid_high=60_000, grid_levels=5,
               core_interval_seconds=2_592_000)
    s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=3), "mid_price": Decimal("50000"),
    })
    assert intents == []


def test_grid_sell_against_fill_emitted():
    """After deploy, simulate a grid buy fill at level 0 → next tick
    emits a sell at level 1 (45000)."""
    s = HodlPlusPlusStrategy()
    ctx = _ctx(core_fraction=0.7, capital_usd=1000,
               grid_low=40_000, grid_high=60_000, grid_levels=5,
               core_interval_seconds=2_592_000)
    s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    # Simulate fill at grid level 0 (price 40000)
    ctx.state["grid"]["levels"][0]["filled_buy"] = True

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=3), "mid_price": Decimal("50000"),
    })
    sells = [it for it in intents if it.role == "exit"]
    assert len(sells) == 1
    assert sells[0].limit_price == Decimal("45000")
    assert sells[0].size_usd == Decimal("60")
    assert sells[0].client_order_id == "hodlpp-1717-grid-L1-exit"


def test_core_rebalances_after_interval():
    """After the core interval, if the core appreciated past target,
    it's sold back. Core 0.014 units bought at 50000 (700 worth). Price
    rises to 70000 → core value = 980. Target still 700 → sell 280."""
    s = HodlPlusPlusStrategy()
    ctx = _ctx(core_fraction=0.7, capital_usd=1000,
               grid_low=40_000, grid_high=60_000, grid_levels=5,
               core_interval_seconds=2_592_000)
    s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("70000"),
    })
    rebal = [it for it in intents if it.role == "rebalance"]
    assert len(rebal) == 1
    assert rebal[0].size_usd == Decimal("280")
    assert ctx.state["core"]["rebalance_count"] == 2


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


def test_missing_mid_price_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0})


def test_naive_datetime_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2026, 5, 11), "mid_price": Decimal("50000"),
        })


def test_core_fraction_out_of_range_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx(core_fraction=1.5)
    with pytest.raises(ValueError, match="core_fraction"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_grid_low_above_high_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx(grid_low=60_000, grid_high=40_000)
    with pytest.raises(ValueError, match="grid_low"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_grid_levels_below_two_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx(grid_levels=1)
    with pytest.raises(ValueError, match="grid_levels"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_nonpositive_core_interval_raises():
    s = HodlPlusPlusStrategy()
    ctx = _ctx(core_interval_seconds=0)
    with pytest.raises(ValueError, match="core_interval"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = HodlPlusPlusStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = HodlPlusPlusStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_in_compat_regimes():
    """Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]. The
    grid leg pays in chop; the core leg carries the uptrend. Zero in
    TREND_DOWN (grid bleeds, core drawdown)."""
    s = HodlPlusPlusStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct > Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
