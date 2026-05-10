"""Phase 3 Wave 1 Task 2.7 — A7 Z-Score Reversion unit tests.

Mechanic: read price z-score from snapshot.
  z < -entry_threshold (default -2.0): emit buy at mid (entry).
  z > +exit_threshold (default +2.0) AND position > 0: emit sell at
    mid (exit) sized to current position.

Hysteresis: last_signal_kind ∈ {'low', 'high', 'middle'} dedupes
consecutive same-bucket reads. Strategy fires only on transition.

Halal-spot inviolable: every emitted intent has side='long'. Sells
only happen when position > 0.

Snapshot contract: {'mid_price', 'price_z_score'}. Z-score plumbing
(rolling mean+std of price) is a later supporting task in features/.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import Regime, StrategyContext
from trading_sandwich.strategies.mean_reversion.z_score import (
    ZScoreReversionStrategy,
)


def _ctx(
    *,
    strategy_id: int = 707,
    entry_threshold: float = 2.0,
    exit_threshold: float = 2.0,
    entry_size_usd: float = 10,
    capital_usd: float = 100,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="z_score_reversion",
        symbol="BTCUSDT",
        params={
            "entry_threshold": str(entry_threshold),
            "exit_threshold": str(exit_threshold),
            "entry_size_usd": str(entry_size_usd),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- Low-z entry ----------


def test_z_below_negative_threshold_emits_buy():
    s = ZScoreReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "price_z_score": Decimal("-2.5"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "entry"
    assert it.limit_price == Decimal("60000")
    assert it.size_usd == Decimal("10")
    assert it.client_order_id.startswith("zscore-707-")


def test_z_at_negative_threshold_does_not_emit():
    """Strict < requirement: z = -threshold doesn't fire."""
    s = ZScoreReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "price_z_score": Decimal("-2.0"),
    })
    assert intents == []


def test_low_z_records_position_and_signal_state():
    s = ZScoreReversionStrategy()
    ctx = _ctx()

    s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "price_z_score": Decimal("-2.5"),
    })

    assert ctx.state["position_size_usd"] == "10"
    assert ctx.state["last_signal_kind"] == "low"


def test_consecutive_low_z_emits_only_once():
    s = ZScoreReversionStrategy()
    ctx = _ctx()

    first = s.tick(ctx, snapshot={
        "mid_price": Decimal("60000"),
        "price_z_score": Decimal("-2.5"),
    })
    assert len(first) == 1

    second = s.tick(ctx, snapshot={
        "mid_price": Decimal("59500"),
        "price_z_score": Decimal("-3.0"),
    })
    assert second == []


# ---------- High-z exit ----------


def test_z_above_positive_threshold_with_position_emits_sell():
    s = ZScoreReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "middle",
    })

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "price_z_score": Decimal("2.5"),
    })

    assert len(intents) == 1
    assert intents[0].side == "long"
    assert intents[0].role == "exit"
    assert intents[0].size_usd == Decimal("10")


def test_high_z_with_no_position_emits_nothing():
    """Halal-spot inviolable: never open a short."""
    s = ZScoreReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "price_z_score": Decimal("2.5"),
    })
    assert intents == []


def test_high_z_exit_clears_position():
    s = ZScoreReversionStrategy()
    ctx = _ctx(state={
        "position_size_usd": "10",
        "last_signal_kind": "middle",
    })

    s.tick(ctx, snapshot={
        "mid_price": Decimal("65000"),
        "price_z_score": Decimal("2.5"),
    })
    assert ctx.state["position_size_usd"] == "0"
    assert ctx.state["last_signal_kind"] == "high"


# ---------- Middle-z does nothing ----------


def test_middle_z_emits_nothing():
    s = ZScoreReversionStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("62000"),
        "price_z_score": Decimal("0.5"),
    })
    assert intents == []
    assert ctx.state["last_signal_kind"] == "middle"


# ---------- Asymmetric thresholds ----------


def test_asymmetric_thresholds_supported():
    """Operator can set different entry/exit thresholds: e.g. enter
    at -2.0 sigma, exit at +1.5."""
    s = ZScoreReversionStrategy()
    ctx = _ctx(
        entry_threshold=2.0,
        exit_threshold=1.5,
        state={"position_size_usd": "10", "last_signal_kind": "middle"},
    )

    intents = s.tick(ctx, snapshot={
        "mid_price": Decimal("63000"),
        "price_z_score": Decimal("1.6"),
    })
    assert len(intents) == 1
    assert intents[0].role == "exit"


# ---------- Param + snapshot validation ----------


def test_missing_z_score_raises():
    s = ZScoreReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("60000")})


def test_missing_mid_price_raises():
    s = ZScoreReversionStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"price_z_score": Decimal("-2.5")})


def test_negative_thresholds_raise():
    s = ZScoreReversionStrategy()
    ctx = _ctx(entry_threshold=-1.0)
    with pytest.raises(ValueError, match="threshold"):
        s.tick(ctx, snapshot={
            "mid_price": Decimal("60000"),
            "price_z_score": Decimal("0"),
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = ZScoreReversionStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = ZScoreReversionStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_range_regimes():
    """Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET]."""
    s = ZScoreReversionStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    assert rv.monthly_return_pct > Decimal("0")
    assert rq.monthly_return_pct > Decimal("0")
    assert tu.monthly_return_pct == Decimal("0")
    assert td.monthly_return_pct == Decimal("0")
