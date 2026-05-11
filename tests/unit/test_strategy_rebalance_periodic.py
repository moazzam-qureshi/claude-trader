"""Phase 3 Wave 1 Task 2.14 — C1 Periodic Rebalancing unit tests.

Single-symbol periodic rebalancing: keep this symbol's position value
at target_fraction of the strategy's allocated capital, resetting on a
calendar interval. The "portfolio" being balanced is {this asset, cash}.

  target_value = target_fraction * capital_allocated_usd
  actual_value = position_units * mid_price
  delta = target_value - actual_value
  delta > 0 → buy delta worth at mid (rebalance entry)
  delta < 0 → sell |delta| worth at mid (rebalance exit, capped)

Interval gating + no-catch-up after worker downtime: same as the DCA
family. First tick rebalances immediately (establishes the position).

Halal-spot inviolable: every emitted intent has side='long'. A sell
only ever reduces an existing long; sell value capped at position.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.
State: position_units, rebalance_count, last_rebalance_at (iso).
OrderIntent.role is 'rebalance' for both buys and sells (it's the
same operation in either direction).

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
from trading_sandwich.strategies.rebalance.periodic import (
    PeriodicRebalanceStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 1414,
    target_fraction: float = 0.5,
    interval_seconds: int = 2_592_000,  # ~monthly
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="rebalance_periodic",
        symbol="BTCUSDT",
        params={
            "target_fraction": str(target_fraction),
            "interval_seconds": interval_seconds,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick: establish the target position ----------


def test_first_tick_buys_to_target_fraction():
    """target_fraction 0.5, capital 1000 → target_value = 500. Empty
    position → buy 500 worth at mid."""
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000)

    intents = s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "limit"
    assert it.role == "rebalance"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("500")
    assert it.client_order_id.startswith("rebper-1414-")


def test_first_tick_records_state():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000)

    s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})

    # 500 USD at 50000 → 0.01 units
    assert Decimal(ctx.state["position_units"]) == Decimal("0.01")
    assert ctx.state["rebalance_count"] == 1
    assert ctx.state["last_rebalance_at"] == _T0.isoformat()


# ---------- Subsequent rebalance: asset grew → sell back to target ----------


def test_asset_appreciated_sells_excess():
    """0.01 units bought at 50000. Price rises to 80000 → actual =
    0.01*80000 = 800. target = 0.5*1000 = 500 → delta = -300 → sell
    300 worth (rebalance exit)."""
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000,
               interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("80000"),
    })

    assert len(intents) == 1
    it = intents[0]
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.size_usd == Decimal("300")
    assert ctx.state["rebalance_count"] == 2
    # units reduced: 0.01 - 300/80000
    assert Decimal(ctx.state["position_units"]) < Decimal("0.01")


def test_asset_depreciated_buys_back_to_target():
    """0.01 units at 50000. Price falls to 30000 → actual = 300.
    target = 500 → delta = +200 → buy 200 worth."""
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000,
               interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("30000"),
    })

    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd == Decimal("200")
    # units increased: 0.01 + 200/30000
    assert Decimal(ctx.state["position_units"]) > Decimal("0.01")


def test_on_target_emits_nothing_but_advances_count():
    s = PeriodicRebalanceStrategy()
    # 0.01 units at 50000 = 500 = target exactly
    ctx = _ctx(target_fraction=0.5, capital_usd=1000,
               interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("50000"),
    })
    assert intents == []
    assert ctx.state["rebalance_count"] == 2


def test_sell_capped_at_position_value():
    s = PeriodicRebalanceStrategy()
    # Tiny position 0.0001 units; even a huge target shortfall can't
    # make us sell more than we hold.
    ctx = _ctx(target_fraction=0.0001, capital_usd=1000,
               interval_seconds=2_592_000, state={
        "position_units": "0.0001",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=31), "mid_price": Decimal("100000"),
    })
    # actual = 0.0001*100000 = 10; target = 0.0001*1000 = 0.1 →
    # delta = -9.9 → sell 9.9 (within the 10 held). No short.
    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd <= Decimal("10")
    assert Decimal(ctx.state["position_units"]) >= Decimal("0")


# ---------- Interval gating ----------


def test_before_interval_emits_nothing():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=5), "mid_price": Decimal("80000"),
    })
    assert intents == []


def test_no_catch_up_after_downtime():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(target_fraction=0.5, capital_usd=1000,
               interval_seconds=2_592_000, state={
        "position_units": "0.01",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=120), "mid_price": Decimal("50000"),
    })
    # One rebalance (on-target → no order), count advances once.
    assert intents == []
    assert ctx.state["rebalance_count"] == 2


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000")})


def test_missing_mid_price_raises():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0})


def test_naive_datetime_raises():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2026, 5, 11), "mid_price": Decimal("50000"),
        })


def test_target_fraction_out_of_range_raises():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(target_fraction=1.5)
    with pytest.raises(ValueError, match="target_fraction"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_nonpositive_interval_raises():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx(interval_seconds=0)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = PeriodicRebalanceStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_positive_everywhere():
    """Spec §6.2 compat: ["*"]. Rebalancing harvests volatility — best
    in choppy regimes, modest in trends. Positive everywhere."""
    s = PeriodicRebalanceStrategy()

    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    td = s.expected_return_for_regime(Regime.TREND_DOWN)

    for r in (rv, rq, tu, td):
        assert r.monthly_return_pct > Decimal("0")
    assert rv.monthly_return_pct > tu.monthly_return_pct
