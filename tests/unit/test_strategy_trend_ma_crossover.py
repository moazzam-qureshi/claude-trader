"""Phase 3 Wave 1 Task 2.18 — D1 MA Crossover unit tests.

Binary in/out trend follower: long the asset while the fast MA is
above the slow MA (golden-cross regime), all cash while it's below
(death-cross regime).

  ma_fast > ma_slow  and not in position → buy position_usd at mid
  ma_fast <= ma_slow and     in position → sell everything at mid
  otherwise → no-op

State is binary: in_position (bool) + position_units. The strategy
acts only on the transition, so a sustained golden cross emits one
entry, not one per tick.

Halal-spot inviolable: every emitted intent has side='long'. The exit
sells the held position to cash; never opens a short.

Snapshot contract: {'mid_price': Decimal, 'ma_fast': Decimal,
'ma_slow': Decimal}. The features stack provides EMAs; the supporting
task maps them to ma_fast/ma_slow.

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
from trading_sandwich.strategies.trend.ma_crossover import (
    MaCrossoverStrategy,
)


def _ctx(
    *,
    strategy_id: int = 1818,
    position_usd: float = 100,
    capital_usd: float = 200,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="trend_ma_crossover",
        symbol="BTCUSDT",
        params={"position_usd": str(position_usd)},
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Golden cross → enter ----------


def test_fast_above_slow_from_flat_enters():
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=100)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"),
        "ma_fast": Decimal("49000"),
        "ma_slow": Decimal("47000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "limit"
    assert it.role == "entry"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("100")
    assert it.client_order_id.startswith("trndma-1818-")
    assert ctx.state["in_position"] is True
    # 100 USD at 50000 → 0.002 units
    assert Decimal(ctx.state["position_units"]) == Decimal("0.002")


def test_already_in_position_sustained_golden_cross_noop():
    """The fast MA stays above the slow — no new entry."""
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("52000"),
        "ma_fast": Decimal("50000"),
        "ma_slow": Decimal("47000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is True


# ---------- Death cross → exit ----------


def test_fast_below_slow_while_in_position_exits():
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("48000"),
        "ma_fast": Decimal("46000"),
        "ma_slow": Decimal("47000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    assert it.limit_price == Decimal("48000")
    # Sells the whole 0.002 units → 0.002*48000 = 96 worth
    assert it.size_usd == Decimal("96")
    assert ctx.state["in_position"] is False
    assert Decimal(ctx.state["position_units"]) == Decimal("0")


def test_flat_and_death_cross_noop():
    """Not in position, fast below slow → nothing to do (we never
    short)."""
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=100)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("48000"),
        "ma_fast": Decimal("46000"),
        "ma_slow": Decimal("47000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is False


def test_equal_mas_treated_as_not_bullish():
    """ma_fast == ma_slow → not a golden cross → if in position, exit."""
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("48000"),
        "ma_fast": Decimal("47000"),
        "ma_slow": Decimal("47000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "exit"


# ---------- Round trip ----------


def test_enter_then_exit_then_re_enter():
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=100)

    # Enter
    s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"), "ma_fast": Decimal("49000"),
        "ma_slow": Decimal("47000"),
    })
    assert ctx.state["in_position"] is True
    # Exit
    s.tick(ctx, snapshot={
        "mid_price": Decimal("48000"), "ma_fast": Decimal("46000"),
        "ma_slow": Decimal("47000"),
    })
    assert ctx.state["in_position"] is False
    # Re-enter
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("51000"), "ma_fast": Decimal("50000"),
        "ma_slow": Decimal("48000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert ctx.state["in_position"] is True
    assert ctx.state["entry_count"] == 2
    assert ctx.state["exit_count"] == 1


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = MaCrossoverStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"ma_fast": Decimal("49000"), "ma_slow": Decimal("47000")})


def test_missing_ma_fast_raises():
    s = MaCrossoverStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "ma_slow": Decimal("47000")})


def test_missing_ma_slow_raises():
    s = MaCrossoverStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "ma_fast": Decimal("49000")})


def test_nonpositive_position_usd_raises():
    s = MaCrossoverStrategy()
    ctx = _ctx(position_usd=0)
    with pytest.raises(ValueError, match="position_usd"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("50000"), "ma_fast": Decimal("49000"),
            "ma_slow": Decimal("47000"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = MaCrossoverStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = MaCrossoverStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_only_in_trend_up():
    """Spec §6.2 compat: [TREND_UP]. The golden cross is a sustained
    uptrend signal — it doesn't help in chop (whipsaws) or downtrend
    (no long)."""
    s = MaCrossoverStrategy()

    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert tu.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct == Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
