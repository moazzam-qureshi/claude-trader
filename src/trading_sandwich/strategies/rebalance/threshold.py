"""C2 Threshold Rebalancing — Phase 3 Wave 1 Task 2.15.

Same single-symbol gap-close as C1 Periodic Rebalancing, but the
trigger is drift magnitude rather than a calendar interval:

  target_value = target_fraction * capital_allocated_usd
  actual_value = position_units * mid
  drift = |actual_value - target_value| / target_value
  drift > drift_threshold  → rebalance to target (buy or sell, capped)
  else                     → no-op

drift_threshold default 0.15 — Shrimpy's research found ~15% the
sweet spot between trading-cost drag and tracking error.

First tick: empty position → drift = 1.0 > threshold → establishes
the target position.

Halal-spot inviolable: side='long' on every intent. Sell value
capped at the held value — never goes short. Position units estimated
as size_usd / mid on a buy; fill-delivery plumbing corrects later.

Snapshot contract: {'mid_price': Decimal}. (No 'now' — purely
drift-driven.) State: position_units, rebalance_count.

Spec §6.2 compat: ["*"] — universal.
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
from trading_sandwich.strategies.rebalance._base import rebalance_toward_value


_COID_PREFIX = "rebthr"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal]:
    try:
        target_fraction = Decimal(str(params["target_fraction"]))
        drift_threshold = Decimal(str(params["drift_threshold"]))
    except KeyError as e:
        raise KeyError(
            f"rebalance_threshold params missing required key: {e}"
        ) from e
    if target_fraction <= Decimal("0") or target_fraction > Decimal("1"):
        raise ValueError(
            f"target_fraction must be in (0, 1], got {target_fraction}"
        )
    if drift_threshold <= Decimal("0"):
        raise ValueError(f"drift_threshold must be > 0, got {drift_threshold}")
    return target_fraction, drift_threshold


class ThresholdRebalanceStrategy(Strategy):
    """C2 Threshold Rebalancing — rebalance only on >X% drift."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("rebalance_threshold requires snapshot['mid_price']")
        mid = Decimal(str(snapshot["mid_price"]))
        target_fraction, drift_threshold = _read_params(ctx.params)

        count = int(ctx.state.get("rebalance_count", 0))
        units = Decimal(ctx.state.get("position_units", "0"))
        target_value = target_fraction * ctx.capital_allocated_usd

        actual_value = units * mid
        if target_value > Decimal("0"):
            drift = abs(actual_value - target_value) / target_value
        else:
            drift = Decimal("0")

        if drift <= drift_threshold:
            ctx.state["rebalance_count"] = count
            ctx.state["position_units"] = str(units)
            return []

        intents, new_units = rebalance_toward_value(
            ctx=ctx, target_value=target_value, mid=mid, units=units,
            coid_prefix=_COID_PREFIX, seq=count,
        )
        ctx.state["rebalance_count"] = count + 1
        ctx.state["position_units"] = str(new_units)
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: ["*"]. Only acts on big moves.
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.022"),
                    confidence=0.45,
                    rationale="Wide drift swings = profitable rebalances",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.008"),
                    confidence=0.4,
                    rationale="Drift rarely clears 15% — few trades, low drag",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.007"),
                    confidence=0.4,
                    rationale="Trims winners on the 15% breaches",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.006"),
                    confidence=0.35,
                    rationale="Buys 15%-deep dips back to target",
                )
