"""Phase 3 Wave 1 Task 2.24 — F1 Halving Cycle Positioning unit tests.

Mechanical capital deployment by Bitcoin halving-cycle phase. The
~4-year cycle (1458 days) is split into four phases measured from the
last halving date:

  accumulation : days   0 ..  182  (≈ 0–6 months post-halving)
  bull         : days 183 ..  547  (≈ 6–18 months)
  distribution : days 548 ..  730  (≈ 18–24 months)
  bear         : days 731 .. 1457  (≈ 24–48 months)

Each phase has a target position fraction; the strategy rebalances
the position toward (phase_fraction * capital) on a slow cadence.
Multi-cycle: days_since_halving is taken mod 1458 so a single
last_halving_date covers all future cycles.

  target_value = phase_fractions[phase(days_since_halving % 1458)] * capital
  then close the gap to actual position value (rebalance/_base.py)

Halal-spot inviolable: every emitted intent has side='long'. The
trim-down's sell value caps at the held value — never goes short.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.
State: position_units, rebalance_count, last_rebalance_at (iso).

Spec §6.2 compat: ["*"].
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
from trading_sandwich.strategies.cycle.halving_position import (
    HalvingCyclePositioningStrategy,
)


# A fixed reference halving date for tests.
_HALVING = datetime(2024, 4, 20, 0, 0, 0, tzinfo=timezone.utc)

_DEFAULT_FRACTIONS = {
    "accumulation": "0.7",
    "bull": "0.9",
    "distribution": "0.3",
    "bear": "0.2",
}


def _ctx(
    *,
    strategy_id: int = 2424,
    last_halving_date: str = "2024-04-20",
    phase_fractions: dict | None = None,
    interval_seconds: int = 604_800,  # weekly
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="cycle_halving",
        symbol="BTCUSDT",
        params={
            "last_halving_date": last_halving_date,
            "phase_fractions": (
                phase_fractions if phase_fractions is not None
                else _DEFAULT_FRACTIONS
            ),
            "interval_seconds": interval_seconds,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Phase → target fraction ----------


def test_accumulation_phase_targets_accumulation_fraction():
    """30 days post-halving → accumulation → 0.7*1000 = 700. Flat →
    buy 700 worth at mid 50000."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000)

    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=30), "mid_price": Decimal("50000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("700")
    assert it.client_order_id.startswith("cychlv-2424-")
    assert ctx.state["rebalance_count"] == 1
    assert Decimal(ctx.state["position_units"]) == Decimal("0.014")


def test_bull_phase_targets_bull_fraction():
    """300 days post-halving → bull → 0.9*1000 = 900."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000)
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=300), "mid_price": Decimal("60000"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("900")


def test_distribution_phase_targets_distribution_fraction():
    """600 days → distribution → 0.3*1000 = 300."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000)
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=600), "mid_price": Decimal("40000"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("300")


def test_bear_phase_targets_bear_fraction():
    """900 days → bear → 0.2*1000 = 200."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000)
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=900), "mid_price": Decimal("30000"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("200")


# ---------- Multi-cycle wrap ----------


def test_next_cycle_wraps_modulo_1458():
    """1458 + 30 days after halving → back into accumulation of the
    next cycle → 0.7*1000 = 700."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000)
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=1458 + 30), "mid_price": Decimal("50000"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("700")


# ---------- Phase transition → rotate ----------


def test_phase_transition_rebalances():
    """Held 0.014 units at 50000 = 700 (accumulation). Time advances
    into bull → target 900 → buy 200 more."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000, interval_seconds=604_800, state={
        "position_units": "0.014",
        "rebalance_count": 1,
        "last_rebalance_at": (_HALVING + timedelta(days=30)).isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=300), "mid_price": Decimal("50000"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("200")
    assert ctx.state["rebalance_count"] == 2


def test_phase_transition_into_bear_trims():
    """Held 0.018 units at 50000 = 900 (bull). Time → bear → target
    200 → sell 700 worth."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(capital_usd=1000, interval_seconds=604_800, state={
        "position_units": "0.018",
        "rebalance_count": 1,
        "last_rebalance_at": (_HALVING + timedelta(days=300)).isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=900), "mid_price": Decimal("50000"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("700")
    assert Decimal(ctx.state["position_units"]) < Decimal("0.018")


# ---------- Interval gating ----------


def test_before_interval_emits_nothing():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "position_units": "0.014",
        "rebalance_count": 1,
        "last_rebalance_at": (_HALVING + timedelta(days=30)).isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _HALVING + timedelta(days=32), "mid_price": Decimal("50000"),
    })
    assert intents == []


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


def test_missing_mid_price_raises():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _HALVING + timedelta(days=30)})


def test_naive_datetime_raises():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2024, 5, 20), "mid_price": Decimal("50000"),
        })


def test_now_before_halving_raises():
    """A tick dated before the halving is incoherent for cycle math."""
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(last_halving_date="2024-04-20")
    with pytest.raises(ValueError, match="halving"):
        s.tick(ctx, snapshot={
            "now": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "mid_price": Decimal("50000"),
        })


def test_missing_phase_fraction_raises():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(phase_fractions={"accumulation": "0.7"})  # missing bull/dist/bear
    with pytest.raises(ValueError, match="phase_fractions"):
        s.tick(ctx, snapshot={
            "now": _HALVING + timedelta(days=30), "mid_price": Decimal("50000"),
        })


def test_phase_fraction_out_of_range_raises():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(phase_fractions={
        "accumulation": "1.5", "bull": "0.9",
        "distribution": "0.3", "bear": "0.2",
    })
    with pytest.raises(ValueError, match="fraction"):
        s.tick(ctx, snapshot={
            "now": _HALVING + timedelta(days=30), "mid_price": Decimal("50000"),
        })


def test_nonpositive_interval_raises():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx(interval_seconds=0)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={
            "now": _HALVING + timedelta(days=30), "mid_price": Decimal("50000"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = HalvingCyclePositioningStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_positive_everywhere():
    """Spec §6.2 compat: ["*"]. A calendar-driven cycle overlay —
    modest positive in every regime."""
    s = HalvingCyclePositioningStrategy()
    for r in (Regime.TREND_UP, Regime.TREND_DOWN,
              Regime.RANGE_VOLATILE, Regime.RANGE_QUIET):
        assert s.expected_return_for_regime(r).monthly_return_pct > Decimal("0")
