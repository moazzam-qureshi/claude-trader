"""Phase 3 Wave 1 Task 2.13 — B7 Drawdown-Tier Accumulation unit tests.

Event-driven accumulation keyed off drawdown from a rolling ATH. The
strategy tracks the running high (self-maintained from mid_price) and
deploys a chunk of capital at each configured tier — e.g. 30/50/65/
80% below ATH — once per drawdown episode. Tiers re-arm when price
recovers to within reset_threshold_pct of the ATH.

  ath = max(state.ath, mid)
  drawdown = (ath - mid) / ath
  drawdown < reset_threshold_pct → re-arm all tiers
  for tier in sorted-by-drawdown_pct:
    drawdown >= tier.drawdown_pct and tier not fired → buy tier.deploy_usd

If price gaps down past several tiers in one tick, all newly-triggered
tiers fire that tick.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. Accumulation only — never sells.

Snapshot contract: {'mid_price': Decimal}.

Spec §6.2 compat: ["*"] — event-driven, best in bears.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    StrategyContext,
)
from trading_sandwich.strategies.dca.drawdown_tier import (
    DrawdownTierStrategy,
)


_DEFAULT_TIERS = [
    {"drawdown_pct": "0.30", "deploy_usd": "50"},
    {"drawdown_pct": "0.50", "deploy_usd": "75"},
    {"drawdown_pct": "0.65", "deploy_usd": "100"},
    {"drawdown_pct": "0.80", "deploy_usd": "150"},
]


def _ctx(
    *,
    strategy_id: int = 1313,
    tiers: list | None = None,
    reset_threshold_pct: float = 0.10,
    capital_usd: float = 5000,
    state: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        strategy_type="dca_drawdown_tier",
        symbol="BTCUSDT",
        params={
            "tiers": tiers if tiers is not None else _DEFAULT_TIERS,
            "reset_threshold_pct": str(reset_threshold_pct),
        },
        state=state if state is not None else {},
        capital_allocated_usd=Decimal(str(capital_usd)),
    )


# ---------- First tick establishes ATH, no drawdown yet ----------


def test_first_tick_sets_ath_no_buy():
    s = DrawdownTierStrategy()
    ctx = _ctx()

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("100000")})

    assert intents == []
    assert Decimal(ctx.state["ath"]) == Decimal("100000")
    assert ctx.state["fired_tiers"] == []


def test_new_high_updates_ath():
    s = DrawdownTierStrategy()
    ctx = _ctx(state={"ath": "100000", "fired_tiers": [],
                      "buy_count": 0, "total_deployed_usd": "0"})

    s.tick(ctx, snapshot={"mid_price": Decimal("120000")})
    assert Decimal(ctx.state["ath"]) == Decimal("120000")


# ---------- Crossing one tier ----------


def test_drawdown_crosses_first_tier_deploys():
    """ATH 100000, price drops to 65000 → 35% drawdown → tier 0 (30%)
    fires, tier 1 (50%) not yet."""
    s = DrawdownTierStrategy()
    ctx = _ctx(state={"ath": "100000", "fired_tiers": [],
                      "buy_count": 0, "total_deployed_usd": "0"})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})

    assert len(intents) == 1
    it = intents[0]
    assert isinstance(it, OrderIntent)
    assert it.side == "long"
    assert it.order_type == "market"
    assert it.role == "entry"
    assert it.size_usd == Decimal("50")  # tier 0 deploy_usd
    assert it.limit_price is None
    assert it.client_order_id.startswith("dcadd-1313-")
    assert ctx.state["fired_tiers"] == [0]
    assert ctx.state["buy_count"] == 1
    assert Decimal(ctx.state["total_deployed_usd"]) == Decimal("50")


def test_tier_does_not_re_fire_on_continued_drawdown():
    """Already past 30% tier; price drifts to 67000 (33% drawdown) —
    still only tier 0 territory, already fired → no buy."""
    s = DrawdownTierStrategy()
    ctx = _ctx(state={"ath": "100000", "fired_tiers": [0],
                      "buy_count": 1, "total_deployed_usd": "50"})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("67000")})
    assert intents == []
    assert ctx.state["fired_tiers"] == [0]


# ---------- Crossing additional tier ----------


def test_deeper_drawdown_fires_next_tier_only():
    """Tier 0 fired; price drops to 48000 → 52% drawdown → tier 1 (50%)
    fires, tier 2 (65%) not yet."""
    s = DrawdownTierStrategy()
    ctx = _ctx(state={"ath": "100000", "fired_tiers": [0],
                      "buy_count": 1, "total_deployed_usd": "50"})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("48000")})

    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("75")  # tier 1 deploy_usd
    assert ctx.state["fired_tiers"] == [0, 1]
    assert ctx.state["buy_count"] == 2
    assert Decimal(ctx.state["total_deployed_usd"]) == Decimal("125")


def test_gap_down_past_multiple_tiers_fires_all_in_one_tick():
    """No tiers fired; price gaps from 100000 ATH straight to 18000 →
    82% drawdown → tiers 0,1,2,3 ALL fire this tick."""
    s = DrawdownTierStrategy()
    ctx = _ctx(state={"ath": "100000", "fired_tiers": [],
                      "buy_count": 0, "total_deployed_usd": "0"})

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("18000")})

    assert len(intents) == 4
    sizes = sorted(it.size_usd for it in intents)
    assert sizes == [Decimal("50"), Decimal("75"), Decimal("100"), Decimal("150")]
    assert ctx.state["fired_tiers"] == [0, 1, 2, 3]
    assert ctx.state["buy_count"] == 4
    assert Decimal(ctx.state["total_deployed_usd"]) == Decimal("375")


# ---------- Recovery re-arms tiers ----------


def test_recovery_near_ath_rearms_tiers():
    """Tiers 0,1 fired during a drawdown; price recovers to 92000
    (8% below ATH 100000 < reset_threshold 10%) → tiers re-armed.
    No buy this tick (we're near the high, no tier triggered)."""
    s = DrawdownTierStrategy()
    ctx = _ctx(reset_threshold_pct=0.10, state={
        "ath": "100000", "fired_tiers": [0, 1],
        "buy_count": 2, "total_deployed_usd": "125",
    })

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("92000")})
    assert intents == []
    assert ctx.state["fired_tiers"] == []


def test_rearmed_then_new_drawdown_fires_again():
    """After re-arm, a fresh 35% drawdown fires tier 0 again."""
    s = DrawdownTierStrategy()
    ctx = _ctx(reset_threshold_pct=0.10, state={
        "ath": "100000", "fired_tiers": [],
        "buy_count": 2, "total_deployed_usd": "125",
    })

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("65000")})
    assert len(intents) == 1
    assert intents[0].size_usd == Decimal("50")
    assert ctx.state["fired_tiers"] == [0]
    assert ctx.state["buy_count"] == 3
    assert Decimal(ctx.state["total_deployed_usd"]) == Decimal("175")


# ---------- New ATH while in drawdown ----------


def test_new_ath_resets_drawdown_baseline_and_rearms():
    """Tier 0 fired at 65000 below 100000 ATH. Price rallies to a new
    ATH of 110000 — drawdown is now 0% < reset_threshold → re-arm."""
    s = DrawdownTierStrategy()
    ctx = _ctx(reset_threshold_pct=0.10, state={
        "ath": "100000", "fired_tiers": [0],
        "buy_count": 1, "total_deployed_usd": "50",
    })

    intents = s.tick(ctx, snapshot={"mid_price": Decimal("110000")})
    assert intents == []
    assert Decimal(ctx.state["ath"]) == Decimal("110000")
    assert ctx.state["fired_tiers"] == []


# ---------- Param + snapshot validation ----------


def test_missing_mid_price_raises():
    s = DrawdownTierStrategy()
    ctx = _ctx()
    with pytest.raises(KeyError):
        s.tick(ctx, snapshot={})


def test_empty_tiers_raises():
    s = DrawdownTierStrategy()
    ctx = _ctx(tiers=[])
    with pytest.raises(ValueError, match="tiers"):
        s.tick(ctx, snapshot={"mid_price": Decimal("100000")})


def test_tier_drawdown_pct_out_of_range_raises():
    s = DrawdownTierStrategy()
    ctx = _ctx(tiers=[{"drawdown_pct": "1.5", "deploy_usd": "50"}])
    with pytest.raises(ValueError, match="drawdown_pct"):
        s.tick(ctx, snapshot={"mid_price": Decimal("100000")})


def test_tier_nonpositive_deploy_raises():
    s = DrawdownTierStrategy()
    ctx = _ctx(tiers=[{"drawdown_pct": "0.3", "deploy_usd": "0"}])
    with pytest.raises(ValueError, match="deploy_usd"):
        s.tick(ctx, snapshot={"mid_price": Decimal("100000")})


def test_reset_threshold_out_of_range_raises():
    s = DrawdownTierStrategy()
    ctx = _ctx(reset_threshold_pct=1.5)
    with pytest.raises(ValueError, match="reset_threshold"):
        s.tick(ctx, snapshot={"mid_price": Decimal("100000")})


# ---------- Lifecycle hooks ----------


def test_graceful_shutdown_emits_no_intents():
    s = DrawdownTierStrategy()
    ctx = _ctx()
    assert s.graceful_shutdown(ctx) == []


def test_emergency_stop_emits_no_intents():
    s = DrawdownTierStrategy()
    ctx = _ctx()
    assert s.emergency_stop(ctx) == []


# ---------- Expected return for regime ----------


def test_expected_return_emphasizes_bear():
    """Spec: bear markets. Deep drawdowns happen in TREND_DOWN — that's
    when this strategy actually deploys. Positive everywhere (it's
    accumulation), highest in TREND_DOWN."""
    s = DrawdownTierStrategy()

    td = s.expected_return_for_regime(Regime.TREND_DOWN)
    rv = s.expected_return_for_regime(Regime.RANGE_VOLATILE)
    tu = s.expected_return_for_regime(Regime.TREND_UP)
    rq = s.expected_return_for_regime(Regime.RANGE_QUIET)

    for r in (td, rv, tu, rq):
        assert r.monthly_return_pct >= Decimal("0")
    assert td.monthly_return_pct > tu.monthly_return_pct
    assert td.monthly_return_pct >= rv.monthly_return_pct
