"""Phase 3 Wave 1 Task 2.22 — D5 Multi-TF Alignment unit tests.

Long only when three timeframes (1D, 4H, 1H) are all bullish; exit
the moment any one turns bearish.

  enter_signal = bullish_1d and bullish_4h and bullish_1h
  exit_signal  = not (all three bullish)

Halal-spot inviolable: every emitted intent has side='long'. The exit
sells the held position to cash; never opens a short.

Snapshot contract: {'mid_price': Decimal, 'bullish_1d': bool,
'bullish_4h': bool, 'bullish_1h': bool}. The supporting task computes
each timeframe's bias (e.g. price > EMA on that timeframe).

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
from trading_sandwich.strategies.trend.multi_tf_alignment import (
    MultiTfAlignmentStrategy,
)


def _ctx(
    *,
    strategy_id: int = 2222,
    position_usd: float = 100,
    capital_usd: float = 200,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="trend_multi_tf_alignment",
        symbol="BTCUSDT",
        params={"position_usd": str(position_usd)},
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


def _snap(mid="50000", d1=True, h4=True, h1=True):
    return {
        "mid_price": Decimal(mid),
        "bullish_1d": d1,
        "bullish_4h": h4,
        "bullish_1h": h1,
    }


# ---------- All three bullish → enter ----------


def test_all_bullish_from_flat_enters():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100)

    intents = s.tick(ctx, snapshot=_snap("51000", True, True, True))

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("51000")
    assert it.size_usd == Decimal("100")
    assert it.client_order_id.startswith("trnmtf-2222-")
    assert ctx.state["in_position"] is True


def test_all_bullish_already_in_position_holds():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot=_snap("52000", True, True, True))
    assert intents == []
    assert ctx.state["in_position"] is True


# ---------- Any one bearish → no enter / exit ----------


def test_one_timeframe_bearish_from_flat_noop():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100)
    # 1H bearish
    intents = s.tick(ctx, snapshot=_snap("51000", True, True, False))
    assert intents == []
    assert ctx.state["in_position"] is False


def test_one_timeframe_turns_bearish_while_in_position_exits():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    # 4H flips bearish
    intents = s.tick(ctx, snapshot=_snap("49000", True, False, True))
    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    # 0.002 * 49000 = 98
    assert it.size_usd == Decimal("98")
    assert ctx.state["in_position"] is False


def test_all_bearish_while_in_position_exits():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100, state={
        "in_position": True, "position_units": "0.002",
        "entry_count": 1, "exit_count": 0,
    })
    intents = s.tick(ctx, snapshot=_snap("48000", False, False, False))
    assert len(intents) == 1
    assert intents[0].role == "exit"


def test_two_of_three_bullish_from_flat_noop():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100)
    intents = s.tick(ctx, snapshot=_snap("51000", True, True, False))
    assert intents == []


# ---------- Round trip ----------


def test_round_trip():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=100)
    s.tick(ctx, snapshot=_snap("51000", True, True, True))
    assert ctx.state["in_position"] is True
    s.tick(ctx, snapshot=_snap("49000", True, True, False))
    assert ctx.state["in_position"] is False
    intents = s.tick(ctx, snapshot=_snap("52000", True, True, True))
    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert ctx.state["entry_count"] == 2
    assert ctx.state["exit_count"] == 1


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"bullish_1d": True, "bullish_4h": True, "bullish_1h": True})


def test_missing_bullish_1d_raises():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "bullish_4h": True, "bullish_1h": True})


def test_missing_bullish_4h_raises():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "bullish_1d": True, "bullish_1h": True})


def test_missing_bullish_1h_raises():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("51000"), "bullish_1d": True, "bullish_4h": True})


def test_nonpositive_position_usd_raises():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx(position_usd=0)
    with pytest.raises(ValueError, match="position_usd"):
        s.tick(ctx, snapshot=_snap())


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = MultiTfAlignmentStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_only_in_trend_up():
    """Spec §6.2 compat: [TREND_UP]. Three-TF alignment is a high-bar
    'strong trend' filter — zero elsewhere."""
    s = MultiTfAlignmentStrategy()

    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert tu.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct == Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
