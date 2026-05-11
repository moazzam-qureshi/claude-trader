"""Phase 3 Wave 1 Task 2.25 — G1 Volatility Targeting unit tests.

Scale the position inversely to realized volatility so the position's
own vol contribution stays near target_vol_pct of allocated capital.
Distinct from C3 Risk Parity by trigger: G1 has no calendar cadence —
it rebalances whenever vol has moved the implied target past a drift
band, so it tracks vol continuously without churning on tiny moves.

  target_value = target_vol_pct * capital / atr_pct
  clamped to [0, max_fraction * capital]
  delta = target_value - position_units * mid
  |delta| > rebalance_band_pct * capital → rebalance to target
  else → no-op

First tick: empty position → |delta| = target which (with sane
params) clears the band → establishes the position.

Halal-spot inviolable: every emitted intent has side='long'. The
trim-down's sell value caps at the held value — never goes short.

Snapshot contract: {'mid_price': Decimal, 'atr_pct': Decimal} where
atr_pct = ATR / price (a small fraction). State: position_units,
rebalance_count.

Spec §6.2 compat: ["*"].
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.vol_regime.vol_targeting import (
    VolatilityTargetingStrategy,
)


def _ctx(
    *,
    strategy_id: int = 2525,
    target_vol_pct: float = 0.02,
    max_fraction: float = 1.0,
    rebalance_band_pct: float = 0.10,
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="vol_targeting",
        symbol="BTCUSDT",
        params={
            "target_vol_pct": str(target_vol_pct),
            "max_fraction": str(max_fraction),
            "rebalance_band_pct": str(rebalance_band_pct),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick establishes the vol-targeted position ----------


def test_first_tick_low_vol_large_position():
    """target_vol 0.02, capital 1000, atr_pct 0.04 → target_value =
    0.02*1000/0.04 = 500. Clamped to max 1000 → 500. Flat → buy 500
    worth at mid 50000."""
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.02, capital_usd=1000, max_fraction=1.0)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "atr_pct": Decimal("0.04"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("500")
    assert it.client_order_id.startswith("voltgt-2525-")
    assert ctx.state["rebalance_count"] == 1
    assert Decimal(ctx.state["position_units"]) == Decimal("0.01")


def test_high_vol_smaller_position():
    """atr_pct 0.10 → target = 0.02*1000/0.10 = 200."""
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.02, capital_usd=1000, max_fraction=1.0)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "atr_pct": Decimal("0.10"),
    })
    assert intents[0].size_usd == Decimal("200")


def test_target_clamped_to_max():
    """Very low vol would imply > capital — clamp. atr_pct 0.005 →
    0.02*1000/0.005 = 4000, max 1.0 → 1000."""
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.02, capital_usd=1000, max_fraction=1.0)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "atr_pct": Decimal("0.005"),
    })
    assert intents[0].size_usd == Decimal("1000")


# ---------- Small drift → no rebalance ----------


def test_small_target_drift_within_band_is_noop():
    """Held 0.01 units at 50000 = 500 (sized at atr_pct 0.04). atr_pct
    nudges to 0.0405 → target = 0.02*1000/0.0405 ≈ 493.8 → delta ≈ -6.2
    → |delta| ≈ 6.2 < band 100 → no-op."""
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.02, capital_usd=1000, max_fraction=1.0,
               rebalance_band_pct=0.10, state={
        "position_units": "0.01", "rebalance_count": 1,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "atr_pct": Decimal("0.0405"),
    })
    assert intents == []
    assert ctx.state["rebalance_count"] == 1
    assert Decimal(ctx.state["position_units"]) == Decimal("0.01")


# ---------- Big vol shift → rebalance ----------


def test_vol_spike_past_band_trims():
    """Held 0.01 units at 50000 = 500. atr_pct spikes to 0.10 → target
    = 200 → delta = -300 → |delta| 300 > band 100 → sell 300 worth."""
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.02, capital_usd=1000, max_fraction=1.0,
               rebalance_band_pct=0.10, state={
        "position_units": "0.01", "rebalance_count": 1,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "atr_pct": Decimal("0.10"),
    })
    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd == Decimal("300")
    assert ctx.state["rebalance_count"] == 2
    assert Decimal(ctx.state["position_units"]) < Decimal("0.01")


def test_vol_drop_past_band_adds():
    """Held 0.004 units at 50000 = 200. atr_pct drops to 0.04 → target
    = 500 → delta = +300 > band → buy 300 worth."""
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.02, capital_usd=1000, max_fraction=1.0,
               rebalance_band_pct=0.10, state={
        "position_units": "0.004", "rebalance_count": 1,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "atr_pct": Decimal("0.04"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("300")
    assert Decimal(ctx.state["position_units"]) > Decimal("0.004")


def test_sell_capped_at_position_value():
    s = VolatilityTargetingStrategy()
    # Tiny position; very high target_vol and vol shrink target below
    # held value — sell can't exceed held.
    ctx = _ctx(target_vol_pct=0.0001, capital_usd=1000, max_fraction=1.0,
               rebalance_band_pct=0.0001, state={
        "position_units": "0.0001", "rebalance_count": 1,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("100000"), "atr_pct": Decimal("0.50"),
    })
    # actual = 10; target = 0.0001*1000/0.50 = 0.2 → sell 9.8 (within 10)
    assert len(intents) == 1
    assert intents[0].size_usd <= Decimal("10")
    assert Decimal(ctx.state["position_units"]) >= Decimal("0")


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = VolatilityTargetingStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"atr_pct": Decimal("0.04")})


def test_missing_atr_pct_raises():
    s = VolatilityTargetingStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


def test_nonpositive_atr_pct_raises():
    s = VolatilityTargetingStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="atr_pct"):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "atr_pct": Decimal("0")})


def test_nonpositive_target_vol_raises():
    s = VolatilityTargetingStrategy()
    ctx = _ctx(target_vol_pct=0.0)
    with pytest.raises(ValueError, match="target_vol"):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "atr_pct": Decimal("0.04")})


def test_max_fraction_out_of_range_raises():
    s = VolatilityTargetingStrategy()
    ctx = _ctx(max_fraction=2.0)
    with pytest.raises(ValueError, match="max_fraction"):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "atr_pct": Decimal("0.04")})


def test_nonpositive_rebalance_band_raises():
    s = VolatilityTargetingStrategy()
    ctx = _ctx(rebalance_band_pct=0.0)
    with pytest.raises(ValueError, match="rebalance_band"):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "atr_pct": Decimal("0.04")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = VolatilityTargetingStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = VolatilityTargetingStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_positive_everywhere():
    """Spec §6.2 compat: ["*"]. Vol targeting is a smoothing overlay —
    caps drawdowns in spikes, leans in when calm; modest positive in
    every regime."""
    s = VolatilityTargetingStrategy()
    for r in (Regime.TREND_UP, Regime.TREND_DOWN,
              Regime.RANGE_VOLATILE, Regime.RANGE_QUIET):
        assert s.expected_return_for_regime(r).monthly_return_pct > Decimal("0")
