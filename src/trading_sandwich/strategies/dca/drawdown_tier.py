"""B7 Drawdown-Tier Accumulation — Phase 3 Wave 1 Task 2.13.

Event-driven accumulation keyed off drawdown from a rolling ATH. The
strategy tracks the running high (self-maintained from mid_price each
tick) and deploys a chunk of capital at each configured tier — e.g.
30/50/65/80% below ATH — once per drawdown episode. Tiers re-arm when
price recovers to within reset_threshold_pct of the ATH (or sets a
new ATH, which is a 0% drawdown).

Per tick:
  ath = max(state.ath or mid, mid)
  drawdown = (ath - mid) / ath
  if drawdown < reset_threshold_pct: fired_tiers = []      # re-arm
  for i, tier in enumerate(tiers sorted by drawdown_pct asc):
    if drawdown >= tier.drawdown_pct and i not in fired_tiers:
      emit market buy of tier.deploy_usd; fired_tiers.append(i)

A price gap straight through several tiers fires all newly-triggered
tiers that tick.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. Accumulation only — never sells.

Snapshot contract: {'mid_price': Decimal}. (The spec mentions a
"rolling ATH from existing kline data"; self-tracking from mid is the
minimal honest contract — backtest replays real prices so the ATH
converges correctly. A supporting task can later feed a true rolling
ATH in if seeding the running high at deploy time matters.)

State: ath (str), fired_tiers (list[int]), buy_count,
total_deployed_usd.

Spec §6.2 compat: ["*"] — event-driven, best in bears.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)


_COID_PREFIX = "dcadd"


def _parse_tiers(raw: Any) -> list[tuple[Decimal, Decimal]]:
    if not raw:
        raise ValueError("tiers must be a non-empty list")
    tiers: list[tuple[Decimal, Decimal]] = []
    for t in raw:
        dd = Decimal(str(t["drawdown_pct"]))
        deploy = Decimal(str(t["deploy_usd"]))
        if dd <= Decimal("0") or dd >= Decimal("1"):
            raise ValueError(
                f"tier drawdown_pct must be in (0, 1), got {dd}"
            )
        if deploy <= Decimal("0"):
            raise ValueError(f"tier deploy_usd must be > 0, got {deploy}")
        tiers.append((dd, deploy))
    # Sort by drawdown threshold ascending so tier index 0 is the
    # shallowest. (Stable order also makes fired_tiers indices stable.)
    tiers.sort(key=lambda x: x[0])
    return tiers


def _read_params(params: dict[str, Any]) -> tuple[list[tuple[Decimal, Decimal]], Decimal]:
    try:
        tiers = _parse_tiers(params["tiers"])
        reset_threshold = Decimal(str(params["reset_threshold_pct"]))
    except KeyError as e:
        raise KeyError(
            f"dca_drawdown_tier params missing required key: {e}"
        ) from e
    if reset_threshold <= Decimal("0") or reset_threshold >= Decimal("1"):
        raise ValueError(
            f"reset_threshold_pct must be in (0, 1), got {reset_threshold}"
        )
    return tiers, reset_threshold


class DrawdownTierStrategy(Strategy):
    """B7 Drawdown-Tier Accumulation — tiered deploy from ATH."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("dca_drawdown_tier requires snapshot['mid_price']")
        mid = Decimal(str(snapshot["mid_price"]))
        tiers, reset_threshold = _read_params(ctx.params)

        prev_ath = Decimal(ctx.state.get("ath", str(mid)))
        ath = max(prev_ath, mid)
        drawdown = (ath - mid) / ath if ath > Decimal("0") else Decimal("0")

        fired = list(ctx.state.get("fired_tiers", []))
        if drawdown < reset_threshold:
            fired = []

        buy_count = int(ctx.state.get("buy_count", 0))
        total = Decimal(ctx.state.get("total_deployed_usd", "0"))

        intents: list[OrderIntent] = []
        for i, (tier_dd, tier_deploy) in enumerate(tiers):
            if drawdown >= tier_dd and i not in fired:
                intents.append(OrderIntent(
                    symbol=ctx.symbol,
                    order_type="market",
                    size_usd=tier_deploy,
                    client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-tier{i}-{buy_count}",
                    role="entry",
                ))
                fired.append(i)
                buy_count += 1
                total += tier_deploy

        ctx.state["ath"] = str(ath)
        ctx.state["fired_tiers"] = sorted(fired)
        ctx.state["buy_count"] = buy_count
        ctx.state["total_deployed_usd"] = str(total)
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec: bear markets. Deep drawdowns happen in TREND_DOWN.
        match regime:
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.03"),
                    confidence=0.45,
                    rationale="Bears hit the tier ladder; cheap deploys",
                )
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.35,
                    rationale="Choppy lows occasionally trip a shallow tier",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.003"),
                    confidence=0.4,
                    rationale="New ATHs keep re-arming; rarely deploys",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.004"),
                    confidence=0.35,
                    rationale="Quiet range stays near the high; few triggers",
                )
