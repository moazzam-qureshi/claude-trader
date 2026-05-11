"""Phase 3 Wave 1 Task 2.12 — B4 Indicator-Triggered DCA unit tests.

Fixed-dollar accumulation, but the trigger is an RSI threshold breach
(not a calendar interval). DCA fires when RSI < rsi_threshold AND a
cooldown has elapsed since the last fire — so it doesn't buy every
tick while RSI sits below the line.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. Accumulation only — never sells, no overbought
exit (that distinguishes it from A5 RSI Mean Reversion).

Snapshot contract: {'now': datetime (tz-aware), 'rsi': Decimal}.

Spec §6.2 compat: [TREND_DOWN, RANGE_VOLATILE].
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
from trading_sandwich.strategies.dca.indicator_triggered import (
    IndicatorTriggeredDcaStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 1212,
    contribution_usd: float = 15,
    rsi_threshold: float = 30,
    cooldown_seconds: int = 86_400,  # daily
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="dca_indicator",
        symbol="BTCUSDT",
        params={
            "contribution_usd": str(contribution_usd),
            "rsi_threshold": str(rsi_threshold),
            "cooldown_seconds": cooldown_seconds,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- RSI below threshold → fire ----------


def test_rsi_below_threshold_first_fire():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(contribution_usd=15)

    intents = s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("25")})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "market"
    assert it.role == "entry"
    assert it.size_usd == Decimal("15")
    assert it.limit_price is None
    assert it.client_order_id.startswith("dcaind-1212-")


def test_first_fire_records_state():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(contribution_usd=15)

    s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("25")})

    assert ctx.state["buy_count"] == 1
    assert Decimal(ctx.state["total_contributed_usd"]) == Decimal("15")
    assert ctx.state["last_buy_at"] == _T0.isoformat()


def test_rsi_at_threshold_does_not_fire():
    """Strict less-than: RSI must be BELOW the threshold."""
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(rsi_threshold=30)

    intents = s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("30")})
    assert intents == []


def test_rsi_above_threshold_does_not_fire():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(rsi_threshold=30)

    intents = s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("55")})
    assert intents == []


# ---------- Cooldown gating ----------


def test_rsi_still_low_within_cooldown_does_not_fire():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(cooldown_seconds=86_400, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "15",
    })

    # 12 hours later, RSI still below threshold
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(hours=12), "rsi": Decimal("22"),
    })
    assert intents == []
    assert ctx.state["buy_count"] == 1


def test_rsi_low_after_cooldown_fires_again():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(contribution_usd=15, cooldown_seconds=86_400, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "15",
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(hours=25), "rsi": Decimal("22"),
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("15")
    assert ctx.state["buy_count"] == 2
    assert Decimal(ctx.state["total_contributed_usd"]) == Decimal("30")


def test_cooldown_boundary_fires():
    """now == last_buy_at + cooldown → cooldown HAS elapsed → fire."""
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(cooldown_seconds=86_400, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "15",
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(seconds=86_400), "rsi": Decimal("22"),
    })
    assert len(intents) == 1


def test_rsi_recovered_after_cooldown_does_not_fire():
    """Cooldown elapsed but RSI is back above threshold → no fire."""
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(cooldown_seconds=86_400, state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 1,
        "total_contributed_usd": "15",
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=2), "rsi": Decimal("60"),
    })
    assert intents == []


# ---------- Never sells ----------


def test_high_rsi_never_emits_a_sell():
    """B4 is accumulation-only — even at extreme RSI it never sells."""
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(state={
        "last_buy_at": _T0.isoformat(),
        "buy_count": 3,
        "total_contributed_usd": "45",
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=10), "rsi": Decimal("85"),
    })
    assert intents == []


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"rsi": Decimal("25")})


def test_missing_rsi_raises():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0})


def test_naive_datetime_raises():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={"now": datetime(2026, 5, 11), "rsi": Decimal("25")})


def test_nonpositive_contribution_raises():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(contribution_usd=0)
    with pytest.raises(ValueError, match="contribution"):
        s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("25")})


def test_nonpositive_cooldown_raises():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(cooldown_seconds=0)
    with pytest.raises(ValueError, match="cooldown"):
        s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("25")})


def test_rsi_threshold_out_of_range_raises():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx(rsi_threshold=150)
    with pytest.raises(ValueError, match="rsi_threshold"):
        s.tick(ctx, snapshot={"now": _T0, "rsi": Decimal("25")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = IndicatorTriggeredDcaStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_only_in_compat_regimes():
    """Spec §6.2 compat: [TREND_DOWN, RANGE_VOLATILE] — these are where
    RSI<30 events actually happen. Uptrend rarely dips that low; quiet
    range stays mid-band. Zero outside compat."""
    s = IndicatorTriggeredDcaStrategy()

    td = s.expected_return_for_regime(Regime.TREND_DOWN)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)

    assert td.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct == Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
