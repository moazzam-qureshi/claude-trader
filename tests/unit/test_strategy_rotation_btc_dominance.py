"""Phase 3 Wave 1 Task 2.23 — E3 BTC Dominance Rotation unit tests.

Per-symbol rotation by BTC.D direction: when BTC dominance is rising,
hold BTC heavy and alts light; when falling, the reverse. Deployed on
a single symbol, this strategy carries an asset_class tag and sizes
the position large or small depending on whether BTC.D favours that
class right now.

  favourable = (asset_class == "btc" and btc_dominance_rising) or
               (asset_class == "alt" and not btc_dominance_rising)
  target_value = (high_fraction if favourable else low_fraction) * capital
  then close the gap to actual position value (rebalance/_base.py)

Slow cadence: first tick acts immediately, subsequent only after
interval_seconds; no catch-up after worker downtime.

Halal-spot inviolable: every emitted intent has side='long'. The
trim-down sells cap at the held value — never goes short.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal,
'btc_dominance_rising': bool}. The supporting task derives the
direction from the TradingView BTC.D feed. State: position_units,
rebalance_count, last_rebalance_at (iso).

Spec §6.2 compat: ["*"] — always on, slow.
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
from trading_sandwich.strategies.rotation.btc_dominance import (
    BtcDominanceRotationStrategy,
)


_T0 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(
    *,
    strategy_id: int = 2323,
    asset_class: str = "btc",
    high_fraction: float = 0.8,
    low_fraction: float = 0.2,
    interval_seconds: int = 604_800,  # weekly
    capital_usd: float = 1000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="rotation_btc_dominance",
        symbol="BTCUSDT",
        params={
            "asset_class": asset_class,
            "high_fraction": str(high_fraction),
            "low_fraction": str(low_fraction),
            "interval_seconds": interval_seconds,
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- BTC asset, dominance rising → heavy ----------


def test_btc_asset_dominance_rising_goes_heavy():
    """asset_class btc, BTC.D rising → favourable → target = 0.8*1000
    = 800. Flat → buy 800 worth at mid 50000."""
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="btc", high_fraction=0.8, capital_usd=1000)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("50000"),
        "btc_dominance_rising": True,
    })

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.role == "rebalance"
    assert it.limit_price == Decimal("50000")
    assert it.size_usd == Decimal("800")
    assert it.client_order_id.startswith("rotbtc-2323-")
    assert ctx.state["rebalance_count"] == 1
    # 800 USD at 50000 → 0.016 units
    assert Decimal(ctx.state["position_units"]) == Decimal("0.016")


def test_btc_asset_dominance_falling_goes_light():
    """asset_class btc, BTC.D falling → unfavourable → target = 0.2*1000
    = 200. Flat → buy 200 worth."""
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="btc", high_fraction=0.8, low_fraction=0.2,
               capital_usd=1000)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("50000"),
        "btc_dominance_rising": False,
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("200")


# ---------- Alt asset, dominance falling → heavy ----------


def test_alt_asset_dominance_falling_goes_heavy():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="alt", high_fraction=0.8, low_fraction=0.2,
               capital_usd=1000)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("100"),
        "btc_dominance_rising": False,  # alts favoured
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("800")


def test_alt_asset_dominance_rising_goes_light():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="alt", high_fraction=0.8, low_fraction=0.2,
               capital_usd=1000)

    intents = s.tick(ctx, snapshot={
        "now": _T0, "mid_price": Decimal("100"),
        "btc_dominance_rising": True,  # alts out of favour
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("200")


# ---------- Regime flip → rotate ----------


def test_flip_to_unfavourable_trims_position():
    """BTC asset held 0.016 units at 50000 = 800 (heavy). BTC.D flips
    to falling → target drops to 200 → sell 600 worth."""
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="btc", high_fraction=0.8, low_fraction=0.2,
               capital_usd=1000, interval_seconds=604_800, state={
        "position_units": "0.016",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8), "mid_price": Decimal("50000"),
        "btc_dominance_rising": False,
    })
    assert len(intents) == 1
    assert intents[0].role == "rebalance"
    assert intents[0].size_usd == Decimal("600")
    assert Decimal(ctx.state["position_units"]) < Decimal("0.016")


def test_flip_to_favourable_adds_to_position():
    """BTC asset held 0.004 units at 50000 = 200 (light). BTC.D flips
    rising → target up to 800 → buy 600 worth."""
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="btc", high_fraction=0.8, low_fraction=0.2,
               capital_usd=1000, interval_seconds=604_800, state={
        "position_units": "0.004",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })

    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=8), "mid_price": Decimal("50000"),
        "btc_dominance_rising": True,
    })
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("600")
    assert Decimal(ctx.state["position_units"]) > Decimal("0.004")


# ---------- Interval gating ----------


def test_before_interval_emits_nothing():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(interval_seconds=604_800, state={
        "position_units": "0.016",
        "rebalance_count": 1,
        "last_rebalance_at": _T0.isoformat(),
    })
    intents = s.tick(ctx, snapshot={
        "now": _T0 + timedelta(days=2), "mid_price": Decimal("50000"),
        "btc_dominance_rising": False,
    })
    assert intents == []


# ---------- Param + snapshot validation ----------


def test_missing_now_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"mid_price": Decimal("50000"), "btc_dominance_rising": True})


def test_missing_mid_price_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0, "btc_dominance_rising": True})


def test_missing_btc_dominance_rising_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={"now": _T0, "mid_price": Decimal("50000")})


def test_naive_datetime_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx()
    with pytest.raises(ValueError, match="timezone"):
        s.tick(ctx, snapshot={
            "now": datetime(2026, 5, 11), "mid_price": Decimal("50000"),
            "btc_dominance_rising": True,
        })


def test_bad_asset_class_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(asset_class="stock")
    with pytest.raises(ValueError, match="asset_class"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"),
            "btc_dominance_rising": True,
        })


def test_fractions_out_of_range_raise():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(high_fraction=1.5)
    with pytest.raises(ValueError, match="fraction"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"),
            "btc_dominance_rising": True,
        })


def test_low_above_high_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(high_fraction=0.2, low_fraction=0.8)
    with pytest.raises(ValueError, match="fraction"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"),
            "btc_dominance_rising": True,
        })


def test_nonpositive_interval_raises():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx(interval_seconds=0)
    with pytest.raises(ValueError, match="interval"):
        s.tick(ctx, snapshot={
            "now": _T0, "mid_price": Decimal("50000"),
            "btc_dominance_rising": True,
        })


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = BtcDominanceRotationStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_positive_everywhere():
    """Spec §6.2 compat: ["*"]. Rotation is a slow always-on overlay —
    modest positive in every regime."""
    s = BtcDominanceRotationStrategy()

    for r in (Regime.TREND_UP, Regime.TREND_DOWN,
              Regime.RANGE_VOLATILE, Regime.RANGE_QUIET):
        assert s.expected_return_for_regime(r).monthly_return_pct > Decimal("0")
