"""Phase 3 Wave 1 Task 2.10 — B2 Value Averaging unit tests.

Classic value averaging: a linear value path target_value(n) =
base_growth_usd * (n+1) — after n+1 intervals the position should be
worth that. At each interval the strategy buys (or sells) the gap
between the target value and the actual value.

  delta_usd = target_value(interval_count) - position_units * mid_price
  delta > 0 → buy delta worth at mid (entry)
  delta < 0 → sell |delta| worth at mid (exit, capped at position)

Position units are estimated as size_usd / mid_price when a buy is
emitted (assumes a near-mid fill). When fill-delivery plumbing lands
the worker will correct units with the real fill; until then the
estimate is the contract.

Interval gating identical to B1: first tick fires immediately,
subsequent fire only after interval_seconds has elapsed.

Halal-spot inviolable: every emitted intent has side='long'. A sell
only ever reduces an existing long; never a short.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.

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
from trading_sandwich.strategies.dca.value_averaging import (
    ValueAveragingStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 1010,
    base_growth_usd: float = 100,
    interval_seconds: int = 604_800,
    capital_usd: float = 5000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="dca_value_averaging",
        symbol="BTCUSDT",
        params={
            "base_growth_usd": str(base_growth_usd),
            "interval_seconds": interval_seconds,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick: target = base_growth, position empty ----------


def test_first_tick_buys_full_target_value():
    """interval_count=0 → target_value(0) = 100*1 = 100. Position empty →
    buy 100 worth at mid."""
    s = ValueAveragingStrategy()
    ctx = _ctx(base_growth_usd=100)

    intents = s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "limit"
    assert it.role == "entry"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("100")
    assert it.client_order_id.startswith("dcava-1010-")


def test_first_tick_records_estimated_units():
    """100 USD at mid 50000 → 0.002 units estimated."""
    s = ValueAveragingStrategy()
    ctx = _ctx(base_growth_usd=100)

    s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    assert Decimal(ctx.state["position_units"]) == Decimal("0.002")
    assert ctx.state["interval_count"] == 1
    assert Decimal(ctx.state["total_contributed_usd"]) == Decimal("100")
    assert ctx.state["last_action_at"] == _T0.isoformat()


# ---------- Second interval: market flat → buy the increment ----------


def test_second_interval_flat_price_buys_increment_only():
    """After 1 buy of 100 at 50000 (0.002 units), target_value(1) =
    200. Price still 50000 → actual = 0.002*50000 = 100 → delta = 100
    → buy 100 more."""
    s = ValueAveragingStrategy()
    ctx = _ctx(base_growth_usd=100, state={
        "position_units": "0.002",
        "interval_count": 1,
        "total_contributed_usd": "100",
        "last_action_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8),
        "mid_price": Decimal("50000"),
    })

    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert intents[0].size_usd == Decimal("100")
    assert ctx.state["interval_count"] == 2
    # 0.002 + 100/50000 = 0.002 + 0.002 = 0.004
    assert Decimal(ctx.state["position_units"]) == Decimal("0.004")
    assert Decimal(ctx.state["total_contributed_usd"]) == Decimal("200")


# ---------- Market ran ahead → buy LESS ----------


def test_market_up_buys_smaller_increment():
    """After 1 buy of 100 at 50000 (0.002 units), price rises to
    75000. target_value(1) = 200. actual = 0.002*75000 = 150 → delta
    = 50 → buy only 50."""
    s = ValueAveragingStrategy()
    ctx = _ctx(base_growth_usd=100, state={
        "position_units": "0.002",
        "interval_count": 1,
        "total_contributed_usd": "100",
        "last_action_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8),
        "mid_price": Decimal("75000"),
    })

    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert intents[0].size_usd == Decimal("50")
    # 0.002 + 50/75000 = 0.002 + 0.0006666... ; keep loose on precision
    assert Decimal(ctx.state["position_units"]) > Decimal("0.0026")


# ---------- Market ran WAY ahead → SELL down to the value path ----------


def test_market_far_up_sells_excess_value():
    """After 1 buy of 100 at 50000 (0.002 units), price spikes to
    150000. target_value(1) = 200. actual = 0.002*150000 = 300 →
    delta = -100 → SELL 100 worth (exit)."""
    s = ValueAveragingStrategy()
    ctx = _ctx(base_growth_usd=100, state={
        "position_units": "0.002",
        "interval_count": 1,
        "total_contributed_usd": "100",
        "last_action_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8),
        "mid_price": Decimal("150000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    assert it.limit_price == Decimal("150000")
    assert it.size_usd == Decimal("100")
    # interval_count still increments (it's an interval action)
    assert ctx.state["interval_count"] == 2
    # units reduced: 0.002 - 100/150000
    assert Decimal(ctx.state["position_units"]) < Decimal("0.002")


def test_sell_capped_at_position_value():
    """Defensive: never sell more value than the position is worth."""
    s = ValueAveragingStrategy()
    # Tiny position 0.0001 units; target_value much lower than actual
    # would imply a huge sell, but we only hold 0.0001*100000 = 10 worth.
    ctx = _ctx(base_growth_usd=1, state={
        "position_units": "0.0001",
        "interval_count": 5,  # target_value(5) = 6
        "total_contributed_usd": "30",
        "last_action_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8),
        "mid_price": Decimal("100000"),
    })
    # actual = 0.0001*100000 = 10; target = 6 → delta = -4 → sell 4
    # (well within the 10 we hold). No short.
    assert len(intents) == 1
    assert intents[0].role == "exit"
    assert intents[0].size_usd <= Decimal("10")
    assert Decimal(ctx.state["position_units"]) >= Decimal("0")


# ---------- Already on target → no-op ----------


def test_on_target_emits_nothing_but_advances_count():
    """If actual value exactly equals target, no order — but the
    interval count still advances (we 'completed' this interval)."""
    s = ValueAveragingStrategy()
    # 0.004 units at 50000 = 200 = target_value(1)
    ctx = _ctx(base_growth_usd=100, state={
        "position_units": "0.004",
        "interval_count": 1,
        "total_contributed_usd": "200",
        "last_action_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8),
        "mid_price": Decimal("50000"),
    })
    assert intents == []
    assert ctx.state["interval_count"] == 2


# ---------- Interval gating ----------


def test_before_interval_emits_nothing():
    s = ValueAveragingStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "position_units": "0.002",
        "interval_count": 1,
        "total_contributed_usd": "100",
        "last_action_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=2),
        "mid_price": Decimal("50000"),
    })
    assert intents == []


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = ValueAveragingStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


def test_missing_mid_price_raises():
    s = ValueAveragingStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0})


def test_naive_datetime_raises():
    s = ValueAveragingStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2026, 5, 11), "mid_price": Decimal("50000"),
        })


def test_nonpositive_base_growth_raises():
    s = ValueAveragingStrategy()
    ctx = _ctx(base_growth_usd=0)
    with pytest.raises(ValueError, match="base_growth"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_nonpositive_interval_raises():
    s = ValueAveragingStrategy()
    ctx = _ctx(interval_seconds=-1)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = ValueAveragingStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = ValueAveragingStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_ranging():
    """Spec: 'Ranging markets' is value averaging's home — the buy-low/
    sell-high oscillation around the path needs chop. Positive
    everywhere (it's still accumulation-flavored), strongest in
    range regimes."""
    s = ValueAveragingStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    for r in (rv, rq, tu, td):
        assert r.monthly_return_pct >= Decimal("0")
    assert rv.monthly_return_pct > tu.monthly_return_pct
