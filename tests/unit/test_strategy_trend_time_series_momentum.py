"""Phase 3 Wave 1 Task 2.21 — D4 Time-Series Momentum unit tests.

The simplest possible trend filter: long while price is above the
N-day moving average, all cash while it's at or below.

  enter_signal = mid > ma_n
  exit_signal  = mid <= ma_n

Halal-spot inviolable: every emitted intent has side='long'. The exit
sells the held position to cash; never opens a short.

Snapshot contract: {'mid_price': Decimal, 'ma_n': Decimal} where ma_n
is the N-day moving average. The supporting task picks N and feeds the
value.

State: in_position, position_units, entry_count, exit_count (shared
trend/_base.py plumbing).

Spec §6.2 compat: [TREND_UP].
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.trend.time_series_momentum import (
    TimeSeriesMomentumStrategy,
)


def _ctx(
    *,
    strategy_id: int = 2121,
    position_usd: float = 100,
    capital_usd: float = 200,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="trend_time_series_momentum",
        symbol="BTCUSDT",
        params={"position_usd": str(position_usd)},
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Above MA → enter ----------


def test_price_above_ma_from_flat_enters():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=100)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("51000"),
        "ma_n": Decimal("50000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("51000")
    assert it.size_usd == Decimal("100")
    assert it.client_order_id.startswith("trntsm-2121-")
    assert ctx.state["in_position"] is True


def test_already_in_position_above_ma_holds():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("52000"), "ma_n": Decimal("50000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is True


# ---------- At/below MA → exit ----------


def test_price_below_ma_while_in_position_exits():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("49000"), "ma_n": Decimal("50000"),
    })
    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    # 0.002 * 49000 = 98
    assert it.size_usd == Decimal("98")
    assert ctx.state["in_position"] is False


def test_price_equal_ma_treated_as_below():
    """mid == ma_n → not 'above' → exit if in position."""
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "ma_n": Decimal("50000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "exit"


def test_flat_and_below_ma_noop():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=100)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("49000"), "ma_n": Decimal("50000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is False


def test_round_trip():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=100)
    s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "ma_n": Decimal("50000")})
    assert ctx.state["in_position"] is True
    s.tick(ctx, snapshot={"mid_price": Decimal("49000"), "ma_n": Decimal("50000")})
    assert ctx.state["in_position"] is False
    intents = s.tick(ctx, snapshot={"mid_price": Decimal("52000"), "ma_n": Decimal("50000")})
    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert ctx.state["entry_count"] == 2
    assert ctx.state["exit_count"] == 1


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"ma_n": Decimal("50000")})


def test_missing_ma_n_raises():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000")})


def test_nonpositive_position_usd_raises():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx(position_usd=0)
    with pytest.raises(ValueError, match="position_usd"):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "ma_n": Decimal("50000")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = TimeSeriesMomentumStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_only_in_trend_up():
    """Spec §6.2 compat: [TREND_UP]. Above-MA = uptrend persistence;
    chop whipsaws, downtrend means no long."""
    s = TimeSeriesMomentumStrategy()

    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert tu.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct == Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
