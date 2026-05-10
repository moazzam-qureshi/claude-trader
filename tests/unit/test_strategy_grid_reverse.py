"""Phase 3 Wave 1 Task 2.4 — A4 Reverse Grid unit tests.

A4 is the inverse of A1 Standard Grid. It assumes the operator already
holds inventory and wants to sell into rallies, then rebuy dips:

  First tick: sell LIMIT at every rung at-or-ABOVE mid_price.
  When rung i's sell fills (state['levels'][i]['filled_sell']=True,
  delivered by the worker), the next tick emits a buy at rung i-1
  (rebuy the dip).

OrderIntent.side stays 'long' always (Halal-spot inviolable). The
first-emitted intent has role='exit' (selling existing inventory);
the rebuy intents have role='entry'.

Per-rung state: {price, side: 'sell', submitted, filled_sell,
submitted_rebuy, client_order_id}. The `side` field marks the rung's
INITIAL action (vs A1 where it's 'buy'). The OrderIntent's actual
side stays 'long'.

Compat (§6.2): [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]. Best when
already holding the asset.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.grid.reverse import ReverseGridStrategy


def _ctx(
    *,
    strategy_id: int = 404,
    low: float = 60_000,
    high: float = 70_000,
    levels: int = 5,
    capital_usd: float = 100,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="grid_reverse",
        symbol="BTCUSDT",
        params={
            "low": str(low),
            "high": str(high),
            "levels": levels,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First-tick deployment of the SELL ladder ----------


def test_first_tick_emits_sell_ladder_above_mid():
    """5 levels 60k-70k with mid=65k → rungs at 60k, 62.5k, 65k, 67.5k,
    70k. Sells go on every rung >= mid → 65k, 67.5k, 70k → 3 sells."""
    s = ReverseGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5, capital_usd=100)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    assert len(intents) == 3
    for it in intents:
        assert isinstance(it, OrderIntent)
        assert it.side == "long"  # Halal-spot inviolable
        assert it.order_type == "limit"
        assert it.role == "exit"
        assert it.symbol == "BTCUSDT"
        assert it.size_usd == Decimal("20")  # 100 / 5 levels
    prices = sorted(it.limit_price for it in intents)
    assert prices == [Decimal("65000"), Decimal("67500"), Decimal("70000")]


def test_first_tick_client_order_ids():
    s = ReverseGridStrategy()
    ctx = _ctx(strategy_id=44, low=100, high=200, levels=3, capital_usd=30)

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("100")})

    coids = sorted(it.client_order_id for it in intents)
    assert coids == [
        "gridrev-44-L0-exit",
        "gridrev-44-L1-exit",
        "gridrev-44-L2-exit",
    ]


def test_first_tick_persists_full_per_rung_schema():
    s = ReverseGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5, capital_usd=100)

    s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    levels = ctx.state["levels"]
    assert len(levels) == 5
    for lv in levels:
        assert lv["side"] == "sell"  # rung's initial action
        assert lv["filled_sell"] is False
        assert lv["submitted_rebuy"] is False
        assert "price" in lv
        assert "client_order_id" in lv
    submitted = [lv["submitted"] for lv in levels]
    # Rungs at 60k, 62.5k below mid → not submitted yet; 65k, 67.5k, 70k
    # >= mid → submitted as sells.
    assert submitted == [False, False, True, True, True]


# ---------- Rebuy-against-fill ----------


def _seeded_state() -> StrategyContext:
    s = ReverseGridStrategy()
    ctx = _ctx(low=60_000, high=70_000, levels=5, capital_usd=100)
    s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    return ctx


def test_filled_sell_emits_rebuy_at_next_lower_rung():
    """When rung 4 (70k) sells, rebuy at rung 3 (67.5k)."""
    s = ReverseGridStrategy()
    ctx = _seeded_state()

    ctx.state["levels"][4]["filled_sell"] = True
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    rebuys = [it for it in intents if it.role == "entry"]
    assert len(rebuys) == 1
    rebuy = rebuys[0]
    assert rebuy.side == "long"
    assert rebuy.order_type == "limit"
    assert rebuy.limit_price == Decimal("67500")
    assert rebuy.size_usd == Decimal("20")
    assert rebuy.grid_level == 3
    assert rebuy.client_order_id == "gridrev-404-L3-entry"
    assert ctx.state["levels"][4]["submitted_rebuy"] is True


def test_bottom_rung_fill_emits_no_rebuy():
    """Rung 0 has no rung below it. A fill there is recorded but emits
    no rebuy (the strategy has hit its declared floor)."""
    s = ReverseGridStrategy()
    # Mid below the lowest rung → all 5 levels at-or-above mid → all sells
    # submitted. (Edge-case: usually wouldn't happen with reverse grid but
    # the contract must hold.)
    ctx = _ctx(low=100, high=200, levels=3, capital_usd=30)
    s.tick(ctx, snapshot={"mid_price": Decimal("50")})

    ctx.state["levels"][0]["filled_sell"] = True
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("50")})

    assert intents == []


def test_already_submitted_rebuy_is_not_re_emitted():
    s = ReverseGridStrategy()
    ctx = _seeded_state()

    ctx.state["levels"][4]["filled_sell"] = True
    s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    rebuys = [it for it in intents if it.role == "entry"]
    assert rebuys == []


def test_multiple_fills_emit_one_rebuy_per_filled_rung():
    s = ReverseGridStrategy()
    ctx = _seeded_state()

    ctx.state["levels"][3]["filled_sell"] = True
    ctx.state["levels"][4]["filled_sell"] = True

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    rebuys = [it for it in intents if it.role == "entry"]
    assert len(rebuys) == 2
    by_level = {it.grid_level: it for it in rebuys}
    assert by_level[2].limit_price == Decimal("65000")
    assert by_level[3].limit_price == Decimal("67500")


# ---------- Param validation ----------


def test_levels_below_two_raises():
    s = ReverseGridStrategy()
    ctx = _ctx(low=100, high=200, levels=1)
    with pytest.raises(ValueError, match="levels"):
        s.tick(ctx, snapshot={"mid_price": Decimal("150")})


def test_low_must_be_below_high():
    s = ReverseGridStrategy()
    ctx = _ctx(low=200, high=100, levels=5)
    with pytest.raises(ValueError, match="low"):
        s.tick(ctx, snapshot={"mid_price": Decimal("150")})


def test_missing_mid_price_raises():
    s = ReverseGridStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = ReverseGridStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = ReverseGridStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_range_and_uptrend():
    """A4 compat (§6.2): [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]. All
    three should produce positive expected return; trend-down zero."""
    s = ReverseGridStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct > Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
