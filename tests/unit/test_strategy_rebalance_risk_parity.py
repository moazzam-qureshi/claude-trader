"""Phase 3 Wave 1 Task 2.16 — C3 Risk Parity unit tests.

Single-symbol risk parity: scale the target position inversely to the
symbol's volatility so the dollar-risk (position_value * atr_pct) is
held constant at target_risk_pct of allocated capital.

  target_value = target_risk_pct * capital / atr_pct
  then clamped to [0, max_fraction * capital]
  then close the gap to actual position value (rebalance/_base.py)

  → low vol  → large position
  → high vol → small position
  product target_value * atr_pct ≈ target_risk_pct * capital (constant)

Calendar-cadence rebalance like C1: first tick rebalances immediately,
subsequent only after interval_seconds; no catch-up after downtime.

Halal-spot inviolable: every emitted intent has side='long'. Sell
value capped at the held value — never goes short.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal,
'atr_pct': Decimal} where atr_pct = ATR / price (a small fraction).
State: position_units, rebalance_count, last_rebalance_at (iso).

Spec §6.2 compat: ["*"] — universal.
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
from trading_sandwich.strategies.rebalance.risk_parity import (
    RiskParityStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 1616,
    target_risk_pct: float = 0.02,
    interval_seconds: int = 2_592_000,  # ~monthly
    max_fraction: float = 1.0,
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="rebalance_risk_parity",
        symbol="BTCUSDT",
        params={
            "target_risk_pct": str(target_risk_pct),
            "interval_seconds": interval_seconds,
            "max_fraction": str(max_fraction),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick: size inversely to vol ----------


def test_first_tick_low_vol_large_position():
    """target_risk_pct 0.02, capital 1000, atr_pct 0.04 →
    target_value = 0.02*1000/0.04 = 500. Clamped to max 1000 → 500.
    Empty position → buy 500 worth at mid 50000."""
    s = RiskParityStrategy()
    ctx = _ctx(target_risk_pct=0.02, capital_usd=1000, max_fraction=1.0)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0.04"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("500")
    assert it.client_order_id.startswith("rebrp-1616-")
    assert Decimal(ctx.state["position_units"]) == Decimal("0.01")
    assert ctx.state["rebalance_count"] == 1


def test_high_vol_smaller_position():
    """atr_pct 0.10 → target_value = 0.02*1000/0.10 = 200. Smaller
    position for the same dollar-risk."""
    s = RiskParityStrategy()
    ctx = _ctx(target_risk_pct=0.02, capital_usd=1000, max_fraction=1.0)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0.10"),
    })
    assert intents[0].size_usd == Decimal("200")


def test_target_clamped_to_max_fraction():
    """Very low vol would imply a position bigger than capital — clamp
    to max_fraction * capital. atr_pct 0.005 → 0.02*1000/0.005 = 4000,
    but max_fraction 1.0 → cap at 1000."""
    s = RiskParityStrategy()
    ctx = _ctx(target_risk_pct=0.02, capital_usd=1000, max_fraction=1.0)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0.005"),
    })
    assert intents[0].size_usd == Decimal("1000")


# ---------- Vol regime shift → rebalance ----------


def test_vol_dropped_rebalances_up():
    """Held 0.004 units at 50000 = 200 (sized at atr_pct 0.10). Next
    interval vol drops to 0.04 → target = 500 → buy 300 more."""
    s = RiskParityStrategy()
    ctx = _ctx(target_risk_pct=0.02, capital_usd=1000, max_fraction=1.0,
               interval_seconds=2_592_000, state={
        "position_units": "0.004",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("50000"),
        "atr_pct": Decimal("0.04"),
    })
    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd == Decimal("300")
    assert ctx.state["rebalance_count"] == 2


def test_vol_spiked_rebalances_down():
    """Held 0.01 units at 50000 = 500 (sized at atr_pct 0.04). Next
    interval vol spikes to 0.10 → target = 200 → sell 300 worth."""
    s = RiskParityStrategy()
    ctx = _ctx(target_risk_pct=0.02, capital_usd=1000, max_fraction=1.0,
               interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("50000"),
        "atr_pct": Decimal("0.10"),
    })
    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd == Decimal("300")
    assert Decimal(ctx.state["position_units"]) < Decimal("0.01")


def test_sell_capped_at_position_value():
    s = RiskParityStrategy()
    # Tiny position; high target_risk and high vol would shrink target
    # below held value — sell can't exceed held.
    ctx = _ctx(target_risk_pct=0.0001, capital_usd=1000, max_fraction=1.0,
               interval_seconds=2_592_000, state={
        "position_units": "0.0001",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("100000"),
        "atr_pct": Decimal("0.50"),
    })
    # actual = 10; target = 0.0001*1000/0.50 = 0.2 → sell 9.8 (within 10)
    assert len(intents) == 1
    assert intents[0].size_usd <= Decimal("10")
    assert Decimal(ctx.state["position_units"]) >= Decimal("0")


# ---------- Interval gating ----------


def test_before_interval_emits_nothing():
    s = RiskParityStrategy()
    ctx = _ctx(interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=5), "mid_price": Decimal("50000"),
        "atr_pct": Decimal("0.10"),
    })
    assert intents == []


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = RiskParityStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "atr_pct": Decimal("0.04")})


def test_missing_mid_price_raises():
    s = RiskParityStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0, "atr_pct": Decimal("0.04")})


def test_missing_atr_pct_raises():
    s = RiskParityStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_naive_datetime_raises():
    s = RiskParityStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2026, 5, 11), "mid_price": Decimal("50000"),
            "atr_pct": Decimal("0.04"),
        })


def test_nonpositive_atr_pct_raises():
    s = RiskParityStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="atr_pct"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0"),
        })


def test_nonpositive_target_risk_raises():
    s = RiskParityStrategy()
    ctx = _ctx(target_risk_pct=0.0)
    with pytest.raises(ValueError, match="target_risk"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0.04"),
        })


def test_max_fraction_out_of_range_raises():
    s = RiskParityStrategy()
    ctx = _ctx(max_fraction=2.0)
    with pytest.raises(ValueError, match="max_fraction"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0.04"),
        })


def test_nonpositive_interval_raises():
    s = RiskParityStrategy()
    ctx = _ctx(interval_seconds=0)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"), "atr_pct": Decimal("0.04"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = RiskParityStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = RiskParityStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_positive_everywhere():
    """Spec §6.2 compat: ["*"]. Risk parity caps drawdowns in vol
    spikes and leans in when calm — a smoother ride, modest positive
    everywhere."""
    s = RiskParityStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    for r in (rv, rq, tu, td):
        assert r.monthly_return_pct > Decimal("0")
