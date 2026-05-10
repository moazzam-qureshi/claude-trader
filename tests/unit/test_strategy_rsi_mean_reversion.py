"""Phase 3 Wave 1 Task 2.5 — A5 RSI Mean Reversion unit tests.

Mechanic: read RSI from snapshot.
  RSI < oversold_threshold (default 30): emit a buy at mid_price (entry).
  RSI > overbought_threshold (default 70): emit a sell at mid_price
    (exit) sized to whatever inventory we currently hold (state-tracked).

One entry per oversold-breach event: once we've fired an oversold
buy, no more buys until RSI returns above oversold AND then re-breaches.
Same hysteresis applies to overbought sells.

Halal-spot inviolable: every emitted intent has side='long'. Sells
only happen when state['position_size_usd'] > 0; the strategy never
opens a short.

Snapshot contract: {'mid_price': Decimal, 'rsi': Decimal}.

Spec §2.1 best regime: RANGE_VOLATILE. §6.2 compat: [RANGE_VOLATILE].
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.mean_reversion.rsi import (
    RsiMeanReversionStrategy,
)


def _ctx(
    *,
    strategy_id: int = 505,
    rsi_oversold: float = 30,
    rsi_overbought: float = 70,
    entry_size_usd: float = 10,
    capital_usd: float = 100,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="rsi_mean_reversion",
        symbol="BTCUSDT",
        params={
            "rsi_oversold": str(rsi_oversold),
            "rsi_overbought": str(rsi_overbought),
            "entry_size_usd": str(entry_size_usd),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Oversold entry ----------


def test_rsi_below_oversold_emits_buy_at_mid_price():
    """RSI=25 < oversold=30 → one buy LIMIT at mid_price."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "rsi": Decimal("25"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "limit"
    assert it.role == "entry"
    assert it.limit_price == Decimal("60000")
    assert it.size_usd == Decimal("10")
    assert it.client_order_id.startswith("rsi-505-")


def test_rsi_at_oversold_threshold_does_not_emit():
    """Strict less-than: RSI must be BELOW oversold."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "rsi": Decimal("30"),
    })

    assert intents == []


def test_oversold_entry_records_position_and_signal_state():
    """After an oversold entry: state tracks position_size_usd and
    last_signal_kind='oversold' so consecutive oversold reads don't
    double-fire."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "rsi": Decimal("25"),
    })

    assert ctx.state["position_size_usd"] == "10"
    assert ctx.state["last_signal_kind"] == "oversold"


def test_consecutive_oversold_reads_emit_only_once():
    """Hysteresis: once we've fired on oversold, more oversold reads do
    nothing until RSI returns above oversold."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    first = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"), "rsi": Decimal("25"),
    })
    assert len(first) == 1

    second = s.tick(ctx, snapshot={
        "mid_price": Decimal("59500"), "rsi": Decimal("22"),
    })
    assert second == []


def test_oversold_then_neutral_then_oversold_re_emits():
    """Hysteresis reset: RSI returns to neutral, then re-breaches
    oversold → fresh entry fires."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"), "rsi": Decimal("25"),
    })
    # Returns above oversold
    s.tick(ctx, snapshot={
        "mid_price": Decimal("60500"), "rsi": Decimal("45"),
    })
    # Re-breaches
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("59000"), "rsi": Decimal("28"),
    })

    assert len(intents) == 1
    assert intents[0].role == "entry"
    # Position doubled
    assert ctx.state["position_size_usd"] == "20"


# ---------- Overbought exit ----------


def test_rsi_above_overbought_with_position_emits_sell():
    """RSI=75 > overbought=70 AND we have inventory → one sell at mid."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "neutral",
    })

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "rsi": Decimal("75"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    assert it.limit_price == Decimal("65000")
    assert it.size_usd == Decimal("10")  # entire position


def test_rsi_above_overbought_with_no_position_emits_nothing():
    """No inventory to sell: do NOT emit a short. Halal-spot inviolable."""
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "rsi": Decimal("75"),
    })

    assert intents == []


def test_overbought_exit_clears_position_and_records_signal():
    s = RsiMeanReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "neutral",
    })

    s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"), "rsi": Decimal("75"),
    })

    assert ctx.state["position_size_usd"] == "0"
    assert ctx.state["last_signal_kind"] == "overbought"


def test_consecutive_overbought_reads_emit_only_once():
    s = RsiMeanReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "neutral",
    })

    first = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"), "rsi": Decimal("75"),
    })
    assert len(first) == 1

    # Re-acquire inventory by oversold → neutral path so we have something
    # to potentially sell again.
    ctx.state["position_size_usd"] = "10"

    second = s.tick(ctx, snapshot={
        "mid_price": Decimal("66000"), "rsi": Decimal("80"),
    })
    assert second == []  # Hysteresis blocks


# ---------- Param + snapshot validation ----------


def test_missing_rsi_in_snapshot_raises():
    s = RsiMeanReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("60000")})


def test_missing_mid_price_in_snapshot_raises():
    s = RsiMeanReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"rsi": Decimal("25")})


def test_overbought_must_be_above_oversold():
    s = RsiMeanReversionStrategy()
    ctx = _ctx(rsi_oversold=70, rsi_overbought=30)
    with pytest.raises(ValueError, match="oversold"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("60000"), "rsi": Decimal("50"),
        })


def test_neutral_rsi_emits_nothing():
    s = RsiMeanReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"), "rsi": Decimal("50"),
    })
    assert intents == []


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = RsiMeanReversionStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = RsiMeanReversionStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_only_in_range_volatile():
    """Spec §6.2 compat: [RANGE_VOLATILE] only."""
    s = RsiMeanReversionStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct == Decimal("0")
    assert tu.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
