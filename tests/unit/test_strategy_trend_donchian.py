"""Phase 3 Wave 1 Task 2.19 — D2 Donchian Breakout (Turtle) unit tests.

Binary in/out trend follower, Turtle-style: long on a breakout above
the N-bar high, exit on a break below the M-bar low. The asymmetric
channel (entry-high vs exit-low) is the built-in whipsaw filter.

  mid >= donchian_high and not in position → buy position_usd (entry)
  mid <= donchian_low  and     in position → sell the whole position (exit)
  otherwise → no-op (hold, or stay flat between the bands)

Halal-spot inviolable: every emitted intent has side='long'. The exit
sells the held position to cash; the strategy sits out the downside
rather than shorting.

Snapshot contract: {'mid_price': Decimal, 'donchian_high': Decimal,
'donchian_low': Decimal} where donchian_high is the highest high over
the entry lookback (e.g. 20 bars) and donchian_low the lowest low over
the exit lookback (e.g. 10 bars). The supporting task computes them.

State: in_position, position_units, entry_count, exit_count.

Spec §6.2 compat: [TREND_UP, TREND_DOWN].
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.trend.donchian import DonchianBreakoutStrategy


def _ctx(
    *,
    strategy_id: int = 1919,
    position_usd: float = 100,
    capital_usd: float = 200,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="trend_donchian",
        symbol="BTCUSDT",
        params={"position_usd": str(position_usd)},
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Breakout above N-bar high → enter ----------


def test_break_above_high_from_flat_enters():
    """mid 51000 >= donchian_high 50000 → buy."""
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100)

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("51000"),
        "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("51000")
    assert it.size_usd == Decimal("100")
    assert it.client_order_id.startswith("trndon-1919-")
    assert ctx.state["in_position"] is True


def test_at_high_exactly_enters():
    """mid == donchian_high → that IS the breakout (>=)."""
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("50000"),
        "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "entry"


def test_between_bands_flat_noop():
    """mid below the high, above the low, no position → wait."""
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("47000"),
        "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is False


def test_already_in_position_above_low_holds():
    """In position, mid above the exit-low → hold, no new order."""
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("52000"),
        "donchian_high": Decimal("53000"),
        "donchian_low": Decimal("45000"),
    })
    assert intents == []
    assert ctx.state["in_position"] is True


# ---------- Break below M-bar low → exit ----------


def test_break_below_low_while_in_position_exits():
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("44000"),
        "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    # 0.002 units * 44000 = 88
    assert it.size_usd == Decimal("88")
    assert ctx.state["in_position"] is False
    assert Decimal(ctx.state["position_units"]) == Decimal("0")


def test_at_low_exactly_exits():
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("45000"),
        "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "exit"


def test_flat_below_low_noop():
    """Not in position, mid at/below the low → nothing (never short)."""
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100)
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("44000"),
        "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert intents == []


# ---------- Round trip ----------


def test_enter_then_exit_then_re_enter():
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=100)

    s.tick(ctx, snapshot={
        "mid_price": Decimal("51000"), "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert ctx.state["in_position"] is True
    s.tick(ctx, snapshot={
        "mid_price": Decimal("44000"), "donchian_high": Decimal("50000"),
        "donchian_low": Decimal("45000"),
    })
    assert ctx.state["in_position"] is False
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("53000"), "donchian_high": Decimal("52000"),
        "donchian_low": Decimal("46000"),
    })
    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert ctx.state["entry_count"] == 2
    assert ctx.state["exit_count"] == 1


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = DonchianBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"donchian_high": Decimal("50000"), "donchian_low": Decimal("45000")})


def test_missing_donchian_high_raises():
    s = DonchianBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "donchian_low": Decimal("45000")})


def test_missing_donchian_low_raises():
    s = DonchianBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "donchian_high": Decimal("50000")})


def test_nonpositive_position_usd_raises():
    s = DonchianBreakoutStrategy()
    ctx = _ctx(position_usd=0)
    with pytest.raises(ValueError, match="position_usd"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("51000"), "donchian_high": Decimal("50000"),
            "donchian_low": Decimal("45000"),
        })


def test_low_above_high_raises():
    """donchian_low > donchian_high is incoherent."""
    s = DonchianBreakoutStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="donchian"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("51000"), "donchian_high": Decimal("45000"),
            "donchian_low": Decimal("50000"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = DonchianBreakoutStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = DonchianBreakoutStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_in_trends():
    """Spec §6.2 compat: [TREND_UP, TREND_DOWN]. Breakouts pay in
    strong directional moves either way — long in the uptrend, sitting
    out (and avoiding chop losses) in the downtrend. Zero in ranging."""
    s = DonchianBreakoutStrategy()

    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)

    assert tu.monthly_return_pct > Decimal("0")
    assert td.monthly_return_pct >= Decimal("0")
    assert rv.monthly_return_pct == Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
