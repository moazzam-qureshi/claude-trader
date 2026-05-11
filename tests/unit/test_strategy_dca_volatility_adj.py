"""Phase 3 Wave 1 Task 2.11 — B3 Volatility-Adjusted DCA unit tests.

Calendar-cadence accumulation like B1, but the contribution scales up
with volatility: bigger buys when the market is shaky.

  contribution = base * (1 + (vol_multiplier_max - 1) * atr_pct/100)
  → atr_pct 0   : base
  → atr_pct 100 : base * vol_multiplier_max
  linear in between. base is the floor (we never DCA less than base).

Interval gating + no-catch-up after worker downtime: identical to B1.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. DCA only ever accumulates.

Snapshot contract: {'now': datetime (tz-aware), 'atr_percentile':
Decimal ∈ [0, 100]}.

Spec §6.2 compat: ["*"]. Best in bear markets — vol-up buys cheaper.
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
from trading_sandwich.strategies.dca.volatility_adj import (
    VolatilityAdjustedDcaStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 1111,
    base_contribution_usd: float = 20,
    interval_seconds: int = 604_800,
    vol_multiplier_max: float = 3.0,
    capital_usd: float = 2000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="dca_volatility_adj",
        symbol="BTCUSDT",
        params={
            "base_contribution_usd": str(base_contribution_usd),
            "interval_seconds": interval_seconds,
            "vol_multiplier_max": str(vol_multiplier_max),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Contribution scales with volatility ----------


def test_low_vol_contributes_base():
    """atr_pct=0 → contribution == base."""
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(base_contribution_usd=20)

    intents = s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("0")})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "market"
    assert it.role == "entry"
    assert it.size_usd == Decimal("20")
    assert it.limit_price is None
    assert it.client_order_id.startswith("dcavol-1111-")


def test_mid_vol_scales_halfway():
    """atr_pct=50, max=3 → 20 * (1 + 2*0.5) = 20*2 = 40."""
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(base_contribution_usd=20, vol_multiplier_max=3.0)

    intents = s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("50")})
    assert intents[0].size_usd == Decimal("40")


def test_max_vol_scales_to_multiplier_cap():
    """atr_pct=100, max=3 → 20*3 = 60."""
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(base_contribution_usd=20, vol_multiplier_max=3.0)

    intents = s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("100")})
    assert intents[0].size_usd == Decimal("60")


def test_state_records_actual_contribution():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(base_contribution_usd=20, vol_multiplier_max=3.0)

    s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("100")})

    assert ctx.state["buy_count"] == 1
    assert Decimal(ctx.state["total_contributed_usd"]) == Decimal("60")
    assert ctx.state["last_buy_at"] == _T0.isoformat()


# ---------- Interval gating ----------


def test_before_interval_emits_nothing():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "20",
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=3),
        "atr_percentile": Decimal("50"),
    })
    assert intents == []


def test_after_interval_emits_buy_with_current_vol():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(base_contribution_usd=20, vol_multiplier_max=3.0,
               interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "20",
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8),
        "atr_percentile": Decimal("100"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("60")
    assert ctx.state["buy_count"] == 2
    assert Decimal(ctx.state["total_contributed_usd"]) == Decimal("80")


def test_no_catch_up_after_long_downtime():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "20",
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=30),
        "atr_percentile": Decimal("50"),
    })
    assert len(intents) == 1
    assert ctx.state["buy_count"] == 2


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"atr_percentile": Decimal("50")})


def test_missing_atr_percentile_raises():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0})


def test_naive_datetime_raises():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2026, 5, 11), "atr_percentile": Decimal("50"),
        })


def test_atr_percentile_out_of_range_raises():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="atr_percentile"):
        s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("-5")})


def test_vol_multiplier_below_one_raises():
    """max < 1 would mean vol REDUCES contributions — backwards for
    'larger contributions when vol high'."""
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(vol_multiplier_max=0.5)
    with pytest.raises(ValueError, match="vol_multiplier_max"):
        s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("50")})


def test_nonpositive_base_contribution_raises():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(base_contribution_usd=0)
    with pytest.raises(ValueError, match="base_contribution"):
        s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("50")})


def test_nonpositive_interval_raises():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx(interval_seconds=0)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={"now": _T0, "atr_percentile": Decimal("50")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = VolatilityAdjustedDcaStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_bear_and_volatile():
    """Spec: bear markets. Vol-up buys cheaper assets → highest
    expectation in TREND_DOWN, also good in RANGE_VOLATILE. Positive
    everywhere (still accumulation)."""
    s = VolatilityAdjustedDcaStrategy()

    td = s.expected_return_for_regime(Regime.TREND_DOWN)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)

    for r in (td, rv, tu, rq):
        assert r.monthly_return_pct > Decimal("0")
    assert td.monthly_return_pct >= rv.monthly_return_pct
    assert td.monthly_return_pct > tu.monthly_return_pct
