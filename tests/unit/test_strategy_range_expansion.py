"""Phase 3 Wave 1 Task 2.8 — A8 Range Expansion/Contraction unit tests.

Inverse-vol position sizing. The strategy holds a target position that
shrinks as volatility expands and grows as volatility contracts —
"buy the calm, sell the storm". It scales the existing position toward
that target, but only when the gap is large enough to matter (a
rebalance band, so it doesn't churn every tick).

Target sizing from ATR percentile (0..100):
  target = base_size_usd * (100 - atr_percentile) / 50
  then clamped to [min_size_usd, max_size_usd].
  → pct 0   : 2 * base   (deep calm, max conviction)
  → pct 50  : 1 * base   (neutral)
  → pct 100 : 0          (extreme vol → min_size_usd floor kicks in)

Action:
  delta = target - current_position
  |delta| < rebalance_band_pct * base_size_usd → no-op
  delta > 0 → buy delta at mid (entry, scale-in)
  delta < 0 → sell |delta| at mid (exit, scale-out, capped at position)

Halal-spot inviolable: every emitted intent has side='long'. Sells
only ever reduce an existing long; never opens a short.

Snapshot contract: {'mid_price', 'atr_percentile'} where
atr_percentile ∈ [0, 100].

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET].
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import Regime, StrategyContext
from trading_sandwich.strategies.mean_reversion.range_expansion import (
    RangeExpansionStrategy,
)


def _ctx(
    *,
    strategy_id: int = 808,
    base_size_usd: float = 20,
    min_size_usd: float = 5,
    max_size_usd: float = 40,
    rebalance_band_pct: float = 0.1,
    capital_usd: float = 100,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="range_expansion_contraction",
        symbol="BTCUSDT",
        params={
            "base_size_usd": str(base_size_usd),
            "min_size_usd": str(min_size_usd),
            "max_size_usd": str(max_size_usd),
            "rebalance_band_pct": str(rebalance_band_pct),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Scale-in on low volatility ----------


def test_low_vol_from_flat_scales_in_toward_target():
    """ATR pct=0 → target = 20*(100-0)/50 = 40 (== max). From flat
    position → buy 40 at mid."""
    s = RangeExpansionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("0"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("60000")
    assert it.size_usd == Decimal("40")
    assert it.client_order_id.startswith("rangex-808-")
    assert ctx.state["position_size_usd"] == "40"


def test_mid_vol_target_equals_base():
    """ATR pct=50 → target = 20*(100-50)/50 = 20 (== base)."""
    s = RangeExpansionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("50"),
    })

    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("20")
    assert ctx.state["position_size_usd"] == "20"


def test_target_clamped_to_max():
    """ATR pct=0 with base=30 → raw target = 60, but max=40 → clamp."""
    s = RangeExpansionStrategy()
    ctx = _ctx(base_size_usd=30, max_size_usd=40)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("0"),
    })

    assert intents[0].size_usd == Decimal("40")
    assert ctx.state["position_size_usd"] == "40"


def test_target_clamped_to_min_on_extreme_vol():
    """ATR pct=100 → raw target = 0, but min=5 → clamp up to 5."""
    s = RangeExpansionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("100"),
    })

    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("5")
    assert ctx.state["position_size_usd"] == "5"


# ---------- Scale-out on rising volatility ----------


def test_rising_vol_scales_out_from_existing_position():
    """Held 40, ATR pct rises to 50 → target = 20 → sell 20 at mid."""
    s = RangeExpansionStrategy()
    ctx = _ctx(state={"position_size_usd": "40"})

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "atr_percentile": Decimal("50"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    assert it.limit_price == Decimal("65000")
    assert it.size_usd == Decimal("20")
    assert ctx.state["position_size_usd"] == "20"


def test_extreme_vol_scales_out_to_min_floor():
    """Held 40, ATR pct=100 → target = 5 (min floor) → sell 35."""
    s = RangeExpansionStrategy()
    ctx = _ctx(state={"position_size_usd": "40"})

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "atr_percentile": Decimal("100"),
    })

    assert len(intents) == 1
    assert intents[0].role == "exit"
    assert intents[0].size_usd == Decimal("35")
    assert ctx.state["position_size_usd"] == "5"


def test_sell_never_exceeds_current_position():
    """Defensive invariant: a scale-out can never sell more than the
    held position — that would imply a short. Held 8 with min_size=2:
    ATR pct=100 → raw target 0, clamped to min 2 → delta = -6 → sell 6
    (not more). Position ends at 2, never negative."""
    s = RangeExpansionStrategy()
    ctx = _ctx(min_size_usd=2, state={"position_size_usd": "8"})

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "atr_percentile": Decimal("100"),
    })

    assert len(intents) == 1
    assert intents[0].role == "exit"
    assert intents[0].size_usd == Decimal("6")
    assert ctx.state["position_size_usd"] == "2"


# ---------- Rebalance band — no churn ----------


def test_small_delta_within_band_is_noop():
    """Held 20, ATR pct=50 → target=20 → delta=0 → no-op."""
    s = RangeExpansionStrategy()
    ctx = _ctx(state={"position_size_usd": "20"})

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("50"),
    })
    assert intents == []
    assert ctx.state["position_size_usd"] == "20"


def test_delta_at_band_boundary_is_noop():
    """rebalance_band_pct=0.1, base=20 → band = 2. A delta of exactly 2
    is NOT strictly greater than the band → no-op.

    Held 18, ATR pct=50 → target=20 → delta=+2 → within band → no-op."""
    s = RangeExpansionStrategy()
    ctx = _ctx(state={"position_size_usd": "18"})

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("50"),
    })
    assert intents == []


def test_delta_just_past_band_acts():
    """Held 17, target=20 → delta=+3 > band(2) → buy 3."""
    s = RangeExpansionStrategy()
    ctx = _ctx(state={"position_size_usd": "17"})

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "atr_percentile": Decimal("50"),
    })
    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert intents[0].size_usd == Decimal("3")
    assert ctx.state["position_size_usd"] == "20"


# ---------- Param + snapshot validation ----------


def test_missing_atr_percentile_raises():
    s = RangeExpansionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("60000")})


def test_missing_mid_price_raises():
    s = RangeExpansionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"atr_percentile": Decimal("50")})


def test_min_greater_than_max_raises():
    s = RangeExpansionStrategy()
    ctx = _ctx(min_size_usd=40, max_size_usd=5)
    with pytest.raises(ValueError, match="min_size"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("60000"),
            "atr_percentile": Decimal("50"),
        })


def test_atr_percentile_out_of_range_raises():
    s = RangeExpansionStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="atr_percentile"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("60000"),
            "atr_percentile": Decimal("150"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = RangeExpansionStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = RangeExpansionStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_range_regimes():
    """Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET]."""
    s = RangeExpansionStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
