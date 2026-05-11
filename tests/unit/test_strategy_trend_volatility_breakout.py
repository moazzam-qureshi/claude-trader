"""Phase 3 Wave 1 Task 2.20 — D3 Volatility Breakout unit tests.

Binary in/out trend follower: go long when price breaks above a
reference level by more than k_atr * ATR (a volatility-scaled
breakout), exit when price falls back to or below the reference.

  enter_signal = mid >= reference_price + k_atr * atr
  exit_signal  = mid <= reference_price
  (between → hold or stay flat)

Halal-spot inviolable: every emitted intent has side='long'. The exit
sells the held position to cash; never opens a short.

Snapshot contract: {'mid_price': Decimal, 'reference_price': Decimal,
'atr': Decimal} where reference_price is the prior close / session
open and atr is the ATR in price terms. The supporting task computes
both.

State: in_position, position_units, entry_count, exit_count (shared
trend/_base.py plumbing).

Spec §6.2 compat: [RANGE_QUIET, TREND_UP] — a quiet base that
suddenly expands is the textbook setup.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.trend.volatility_breakout import (
    VolatilityBreakoutStrategy,
)


def _ctx(
    *,
    strategy_id: int = 2020,
    position_usd: float = 100,
    k_atr: float = 1.0,
    capital_usd: float = 200,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="trend_volatility_breakout",
        symbol="BTCUSDT",
        params={"position_usd": str(position_usd), "k_atr": str(k_atr)},
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Vol breakout above reference → enter ----------


def test_break_above_reference_plus_k_atr_enters():
    """ref 50000, atr 1000, k 1.0 → breakout level 51000. mid 51500 >=
    51000 → buy."""
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=1.0)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("51500"),
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("51500")
    assert it.size_usd == Decimal("100")
    assert it.client_order_id.startswith("trnvbo-2020-")
    assert ctx.state["in_position"] is True


def test_at_breakout_level_exactly_enters():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=1.0)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("51000"),
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "entry"


def test_below_breakout_level_flat_noop():
    """Price up but not enough to clear k_atr * ATR → wait."""
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=1.0)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50500"),  # below 51000 breakout level
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is False


def test_in_position_above_reference_holds():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=1.0, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("52000"),
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is True


# ---------- Fall back to reference → exit ----------


def test_fall_to_reference_while_in_position_exits():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=1.0, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("49500"),  # at/below ref 50000
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "exit"
    # 0.002 units * 49500 = 99
    assert intents[0].size_usd == Decimal("99")
    assert ctx.state["in_position"] is False


def test_at_reference_exactly_exits():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=1.0, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"),
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "exit"


# ---------- Custom k_atr ----------


def test_higher_k_requires_bigger_move():
    """k 2.0 → breakout level = ref + 2*atr = 52000. mid 51500 < 52000
    → no entry."""
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=100, k_atr=2.0)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("51500"),
        "reference_price": Decimal("50000"),
        "atr": Decimal("1000"),
    })
    assert intents == []


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"reference_price": Decimal("50000"), "atr": Decimal("1000")})


def test_missing_reference_price_raises():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51500"), "atr": Decimal("1000")})


def test_missing_atr_raises():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51500"), "reference_price": Decimal("50000")})


def test_nonpositive_position_usd_raises():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(position_usd=0)
    with pytest.raises(ValueError, match="position_usd"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("51500"), "reference_price": Decimal("50000"),
            "atr": Decimal("1000"),
        })


def test_nonpositive_k_atr_raises():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx(k_atr=0)
    with pytest.raises(ValueError, match="k_atr"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("51500"), "reference_price": Decimal("50000"),
            "atr": Decimal("1000"),
        })


def test_nonpositive_atr_raises():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="atr"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("51500"), "reference_price": Decimal("50000"),
            "atr": Decimal("0"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = VolatilityBreakoutStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_in_compat_regimes():
    """Spec §6.2 compat: [RANGE_QUIET, TREND_UP]. The setup is a quiet
    base that expands; once in an uptrend the breakouts keep working.
    Zero in volatile range (whipsaws) and downtrend (no long)."""
    s = VolatilityBreakoutStrategy()

    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
