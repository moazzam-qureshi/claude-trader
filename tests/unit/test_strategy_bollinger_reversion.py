"""Phase 3 Wave 1 Task 2.6 — A6 Bollinger Reversion unit tests.

Mechanic: read mid_price + Bollinger upper/lower bands from snapshot.
  mid <= bb_lower → emit one buy at mid (entry).
  mid >= bb_upper AND position > 0 → emit one sell at mid (exit)
    sized to current position.

Hysteresis: last_signal_kind ∈ {'lower', 'upper', 'middle'} dedupes
consecutive same-side breaches; price must return to mid-band before
re-firing on the same side.

Halal-spot inviolable: every emitted intent has side='long'. Sells
only happen when position > 0.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET]. Best in stable vol.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import Regime, StrategyContext
from trading_sandwich.strategies.mean_reversion.bollinger import (
    BollingerReversionStrategy,
)


def _ctx(
    *,
    strategy_id: int = 606,
    entry_size_usd: float = 10,
    capital_usd: float = 100,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="bollinger_reversion",
        symbol="BTCUSDT",
        params={"entry_size_usd": str(entry_size_usd)},
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Lower-band entry ----------


def test_mid_at_or_below_lower_band_emits_buy():
    s = BollingerReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "bb_lower": Decimal("60500"),
        "bb_upper": Decimal("65000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("60000")
    assert it.size_usd == Decimal("10")
    assert it.client_order_id.startswith("bb-606-")


def test_lower_breach_records_position_and_signal_state():
    s = BollingerReversionStrategy()
    ctx = _ctx()

    s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "bb_lower": Decimal("60500"),
        "bb_upper": Decimal("65000"),
    })

    assert ctx.state["position_size_usd"] == "10"
    assert ctx.state["last_signal_kind"] == "lower"


def test_consecutive_lower_breaches_emit_only_once():
    s = BollingerReversionStrategy()
    ctx = _ctx()

    first = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "bb_lower": Decimal("60500"), "bb_upper": Decimal("65000"),
    })
    assert len(first) == 1

    second = s.tick(ctx, snapshot={
        "mid_price": Decimal("59800"),
        "bb_lower": Decimal("60300"), "bb_upper": Decimal("64800"),
    })
    assert second == []


def test_lower_then_middle_then_lower_re_emits():
    s = BollingerReversionStrategy()
    ctx = _ctx()

    s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "bb_lower": Decimal("60500"), "bb_upper": Decimal("65000"),
    })
    # Mid back into the band
    s.tick(ctx, snapshot={
        "mid_price": Decimal("62000"),
        "bb_lower": Decimal("60500"), "bb_upper": Decimal("65000"),
    })
    # Re-breaches lower
    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "bb_lower": Decimal("60500"), "bb_upper": Decimal("65000"),
    })

    assert len(intents) == 1
    assert intents[0].role == "entry"
    assert ctx.state["position_size_usd"] == "20"


# ---------- Upper-band exit ----------


def test_mid_at_or_above_upper_with_position_emits_sell():
    s = BollingerReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "middle",
    })

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "bb_lower": Decimal("60500"),
        "bb_upper": Decimal("65000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "exit"
    assert it.limit_price == Decimal("65000")
    assert it.size_usd == Decimal("10")  # entire position


def test_upper_breach_with_no_position_emits_nothing():
    """Halal-spot inviolable: never open a short."""
    s = BollingerReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "bb_lower": Decimal("60500"),
        "bb_upper": Decimal("65000"),
    })
    assert intents == []


def test_upper_exit_clears_position():
    s = BollingerReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "middle",
    })

    s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "bb_lower": Decimal("60500"),
        "bb_upper": Decimal("65000"),
    })
    assert ctx.state["position_size_usd"] == "0"
    assert ctx.state["last_signal_kind"] == "upper"


# ---------- Middle band ----------


def test_mid_inside_band_emits_nothing():
    s = BollingerReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("62000"),
        "bb_lower": Decimal("60500"),
        "bb_upper": Decimal("65000"),
    })
    assert intents == []
    assert ctx.state["last_signal_kind"] == "middle"


# ---------- Snapshot validation ----------


def test_missing_bb_lower_raises():
    s = BollingerReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("60000"),
            "bb_upper": Decimal("65000"),
        })


def test_missing_bb_upper_raises():
    s = BollingerReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("60000"),
            "bb_lower": Decimal("60500"),
        })


def test_missing_mid_price_raises():
    s = BollingerReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={
            "bb_lower": Decimal("60500"),
            "bb_upper": Decimal("65000"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = BollingerReversionStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = BollingerReversionStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_range_regimes():
    """Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET]. Trends zero."""
    s = BollingerReversionStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
