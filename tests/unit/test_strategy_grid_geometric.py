"""Phase 3 Wave 1 Task 2.3 — A3 Geometric Grid unit tests.

A3 is the percentage-spaced cousin of A1 Standard Grid. Same fixed-top
buy ladder + sell-against-fill mechanic, but rungs are geometrically
spaced (rung i at low * (1 + pct_spacing) ** i) instead of arithmetically
spaced. Better for low-priced alts where a fixed dollar spacing produces
uneven percent moves.

Differs from A2 Infinity Grid in one way: no upward expansion. The grid
has a fixed top at low * (1 + pct_spacing) ** (levels - 1).

spec §2.1: best regime RANGE_VOLATILE / RANGE_QUIET.
spec §6.2 defaults: levels=6, pct_spacing=0.02.
spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET].
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.grid.geometric import GeometricGridStrategy


def _ctx(
    *,
    strategy_id: int = 303,
    low: float = 100,
    pct_spacing: float = 0.02,
    levels: int = 6,
    capital_usd: float = 60,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="grid_geometric",
        symbol="SOLUSDT",
        params={
            "low": str(low),
            "pct_spacing": str(pct_spacing),
            "levels": levels,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First-tick deployment ----------


def test_first_tick_emits_geometric_buy_ladder():
    """6 levels, low=100, pct_spacing=0.02 → prices grow geometrically.
    mid=105 → rungs at 100, 102, 104.04 are <= mid → 3 buys."""
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.02, levels=6, capital_usd=60)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    assert len(intents) == 3
    for it in intents:
        assert it.side == "long"
        assert it.role == "entry"
        assert it.order_type == "limit"
        assert it.size_usd == Decimal("10")  # 60 / 6 levels
    prices = sorted(it.limit_price for it in intents)
    assert prices[0] == Decimal("100")
    assert prices[1] == Decimal("102.00")
    assert prices[2] == Decimal("104.0400")


def test_first_tick_persists_full_per_rung_schema():
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.02, levels=6, capital_usd=60)

    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    levels = ctx.state["levels"]
    assert len(levels) == 6
    for lv in levels:
        assert lv["side"] == "buy"
        assert lv["filled_buy"] is False
        assert lv["submitted_sell"] is False
        assert "price" in lv
        assert "client_order_id" in lv
    submitted = [lv["submitted"] for lv in levels]
    assert submitted == [True, True, True, False, False, False]


def test_client_order_id_format():
    s = GeometricGridStrategy()
    ctx = _ctx(strategy_id=77, low=100, pct_spacing=0.02, levels=3,
               capital_usd=30)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("110")})
    coids = sorted(it.client_order_id for it in intents)
    assert coids == [
        "gridgeo-77-L0-entry",
        "gridgeo-77-L1-entry",
        "gridgeo-77-L2-entry",
    ]


# ---------- Sell-against-fill ----------


def test_filled_buy_emits_sell_at_next_higher_rung():
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.02, levels=6, capital_usd=60)
    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    ctx.state["levels"][0]["filled_buy"] = True
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    sells = [it for it in intents if it.role == "exit"]
    assert len(sells) == 1
    assert sells[0].limit_price == Decimal("102.00")
    assert sells[0].size_usd == Decimal("10")
    assert sells[0].grid_level == 1
    assert sells[0].client_order_id == "gridgeo-303-L1-exit"


def test_top_rung_fill_emits_no_sell():
    s = GeometricGridStrategy()
    # mid above all 6 levels → all submitted as buys
    ctx = _ctx(low=100, pct_spacing=0.02, levels=6, capital_usd=60)
    s.tick(ctx, snapshot={"mid_price": Decimal("200")})

    # Top rung index = 5
    ctx.state["levels"][5]["filled_buy"] = True
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("200")})

    assert intents == []


def test_already_submitted_sell_is_not_re_emitted():
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.02, levels=6, capital_usd=60)
    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    ctx.state["levels"][0]["filled_buy"] = True
    s.tick(ctx, snapshot={"mid_price": Decimal("105")})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("105")})
    assert intents == []


# ---------- Fixed top — no upward expansion (A3 ≠ A2) ----------


def test_no_upward_expansion_when_mid_climbs():
    """Unlike A2 Infinity, A3 has a fixed top. Even when mid rises far
    above the current top rung, no new rungs spawn."""
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.02, levels=6, capital_usd=60)
    s.tick(ctx, snapshot={"mid_price": Decimal("105")})
    initial_count = len(ctx.state["levels"])

    s.tick(ctx, snapshot={"mid_price": Decimal("200")})
    assert len(ctx.state["levels"]) == initial_count


# ---------- Param validation ----------


def test_levels_below_two_raises():
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.02, levels=1)
    with pytest.raises(ValueError, match="levels"):
        s.tick(ctx, snapshot={"mid_price": Decimal("105")})


def test_pct_spacing_must_be_positive():
    s = GeometricGridStrategy()
    ctx = _ctx(low=100, pct_spacing=0.0, levels=6)
    with pytest.raises(ValueError, match="pct_spacing"):
        s.tick(ctx, snapshot={"mid_price": Decimal("105")})


def test_low_must_be_positive():
    s = GeometricGridStrategy()
    ctx = _ctx(low=0, pct_spacing=0.02, levels=6)
    with pytest.raises(ValueError, match="low"):
        s.tick(ctx, snapshot={"mid_price": Decimal("105")})


def test_missing_mid_price_raises():
    s = GeometricGridStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = GeometricGridStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = GeometricGridStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_range_regimes():
    """A3 compat per §6.2: [RANGE_VOLATILE, RANGE_QUIET]. Trend regimes
    should produce zero expected return — the strategy stands down."""
    s = GeometricGridStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
    assert rv.monthly_return_pct >= rq.monthly_return_pct
