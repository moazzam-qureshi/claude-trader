"""Phase 3 Wave 1 Task 2.9 — B1 Calendar DCA unit tests.

Mechanic: fixed-dollar market buy every `interval_seconds`. First tick
fires immediately (no prior buy); subsequent fires only after the
interval has elapsed since the last buy.

Snapshot contract: {'now': datetime (tz-aware)}. The strategy reads
`now` to decide whether the interval has elapsed; the worker will
inject datetime.now(timezone.utc) once snapshot plumbing lands.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. DCA only ever accumulates.

State: {'last_buy_at': iso str, 'buy_count': int,
'total_contributed_usd': str}.

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
from trading_sandwich.strategies.dca.calendar import CalendarDcaStrategy


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 909,
    contribution_usd: float = 25,
    interval_seconds: int = 604_800,  # weekly
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="dca_calendar",
        symbol="BTCUSDT",
        params={
            "contribution_usd": str(contribution_usd),
            "interval_seconds": interval_seconds,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First-tick fires immediately ----------


def test_first_tick_emits_market_buy():
    s = CalendarDcaStrategy()
    ctx = _ctx(contribution_usd=25)

    intents = s.tick(ctx, snapshot={"now": _T0})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "market"
    assert it.role == "entry"
    assert it.size_usd == Decimal("25")
    assert it.limit_price is None
    assert it.client_order_id.startswith("dcacal-909-")


def test_first_tick_records_state():
    s = CalendarDcaStrategy()
    ctx = _ctx(contribution_usd=25)

    s.tick(ctx, snapshot={"now": _T0})

    assert ctx.state["last_buy_at"] == _T0.isoformat()
    assert ctx.state["buy_count"] == 1
    assert ctx.state["total_contributed_usd"] == "25"


# ---------- Interval gating ----------


def test_tick_before_interval_elapsed_emits_nothing():
    s = CalendarDcaStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "25",
    })

    # Only 3 days later
    intents = s.tick(ctx, snapshot={"now": _T0 + timedelta(days=3)})

    assert intents == []
    assert ctx.state["buy_count"] == 1


def test_tick_after_interval_elapsed_emits_buy():
    s = CalendarDcaStrategy()
    ctx = _ctx(contribution_usd=25, interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "25",
    })

    # 8 days later — past the weekly interval
    intents = s.tick(ctx, snapshot={"now": _T0 + timedelta(days=8)})

    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("25")
    assert ctx.state["buy_count"] == 2
    assert ctx.state["total_contributed_usd"] == "50"
    assert ctx.state["last_buy_at"] == (_T0 + timedelta(days=8)).isoformat()


def test_tick_exactly_at_interval_boundary_emits_buy():
    """now == last_buy_at + interval → the interval HAS elapsed → fire."""
    s = CalendarDcaStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "25",
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(seconds=604_800),
    })
    assert len(intents) == 1
    assert ctx.state["buy_count"] == 2


def test_one_second_before_boundary_emits_nothing():
    s = CalendarDcaStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "25",
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(seconds=604_799),
    })
    assert intents == []


def test_multiple_intervals_elapsed_still_one_buy_per_tick():
    """If the worker was down for a month, the next tick fires ONE buy,
    not four. (Catch-up logic would over-deploy; better to resume the
    cadence from now.)"""
    s = CalendarDcaStrategy()
    ctx = _ctx(contribution_usd=25, interval_seconds=604_800, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "25",
    })

    intents = s.tick(ctx, snapshot={"now": _T0 + timedelta(days=30)})

    assert len(intents) == 1
    assert ctx.state["buy_count"] == 2


# ---------- Daily interval ----------


def test_daily_interval():
    s = CalendarDcaStrategy()
    ctx = _ctx(contribution_usd=5, interval_seconds=86_400, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 3,
        "total_contributed_usd": "15",
    })

    intents = s.tick(ctx, snapshot={"now": _T0 + timedelta(days=1, hours=1)})
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("5")
    assert ctx.state["buy_count"] == 4
    assert ctx.state["total_contributed_usd"] == "20"


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = CalendarDcaStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={})


def test_naive_datetime_raises():
    """now must be timezone-aware — comparing naive vs aware crashes."""
    s = CalendarDcaStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={"now": datetime(2026, 5, 11, 12, 0, 0)})


def test_nonpositive_interval_raises():
    s = CalendarDcaStrategy()
    ctx = _ctx(interval_seconds=0)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={"now": _T0})


def test_nonpositive_contribution_raises():
    s = CalendarDcaStrategy()
    ctx = _ctx(contribution_usd=0)
    with pytest.raises(ValueError, match="contribution"):
        s.tick(ctx, snapshot={"now": _T0})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = CalendarDcaStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = CalendarDcaStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_positive_everywhere():
    """DCA is universal accumulation — positive expectation in every
    regime, slightly higher when buying into a downtrend."""
    s = CalendarDcaStrategy()

    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)

    for r in (tu, td, rv, rq):
        assert r.monthly_return_pct > Decimal("0")
    assert td.monthly_return_pct >= tu.monthly_return_pct
