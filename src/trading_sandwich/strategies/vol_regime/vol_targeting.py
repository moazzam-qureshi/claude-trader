"""G1 Volatility Targeting — Phase 3 Wave 1 Task 2.25.

Scale the position inversely to realized volatility so the position's
own vol contribution stays near target_vol_pct of allocated capital.
Distinct from C3 Risk Parity by trigger: G1 has no calendar cadence —
it rebalances whenever vol has moved the implied target past a drift
band, so it tracks vol continuously without churning on tiny moves.

  target_value = target_vol_pct * capital / atr_pct
  clamped to [0, max_fraction * capital]
  delta = target_value - position_units * mid
  |delta| > rebalance_band_pct * capital → rebalance to target
  else → no-op

First tick: empty position → |delta| = target which (with sane
params) clears the band → establishes the position.

Halal-spot inviolable: side='long' on every intent. The trim-down's
sell value caps at the held value — never goes short. Position units
estimated as size_usd / mid on a buy; fill-delivery plumbing corrects
later. Reuses rebalance/_base.py's rebalance_toward_value().

Snapshot contract: {'mid_price': Decimal, 'atr_pct': Decimal} where
atr_pct = ATR / price (a small fraction). The supporting task feeds
atr_14 / close. State: position_units, rebalance_count.

Spec §6.2 compat: ["*"] — a smoothing overlay for every regime.
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


_COID_PREFIX = "voltgt"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    try:
        target_vol = Decimal(str(params["target_vol_pct"]))
        max_fraction = Decimal(str(params["max_fraction"]))
        band_pct = Decimal(str(params["rebalance_band_pct"]))
    except KeyError as e:
        raise KeyError(
            f"vol_targeting params missing required key: {e}"
        ) from e
    if target_vol <= Decimal("0"):
        raise ValueError(f"target_vol_pct must be > 0, got {target_vol}")
    if max_fraction <= Decimal("0") or max_fraction > Decimal("1"):
        raise ValueError(f"max_fraction must be in (0, 1], got {max_fraction}")
    if band_pct <= Decimal("0"):
        raise ValueError(f"rebalance_band_pct must be > 0, got {band_pct}")
    return target_vol, max_fraction, band_pct


class VolatilityTargetingStrategy(Strategy):
    """G1 Volatility Targeting — inverse-vol sizing with a drift band."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "atr_pct"):
            if k not in snapshot:
                raise KeyError(f"vol_targeting requires snapshot[{k!r}]")
        mid = Decimal(str(snapshot["mid_price"]))
        atr_pct = Decimal(str(snapshot["atr_pct"]))
        if atr_pct <= Decimal("0"):
            raise ValueError(f"atr_pct must be > 0, got {atr_pct}")
        target_vol, max_fraction, band_pct = _read_params(ctx.params)

        capital = ctx.capital_allocated_usd
        raw_target = target_vol * capital / atr_pct
        cap = max_fraction * capital
        target_value = min(raw_target, cap)

        count = int(ctx.state.get("rebalance_count", 0))
        units = Decimal(ctx.state.get("position_units", "0"))
        actual_value = units * mid
        delta = target_value - actual_value
        band = band_pct * capital

        if abs(delta) <= band:
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
        # Spec §6.2 compat: ["*"]. A smoothing overlay.
        match regime:
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.35,
                    rationale="Calm → leans in, captures the drift",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.011"),
                    confidence=0.35,
                    rationale="Uptrends are often lower-vol → larger size",
                )
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.008"),
                    confidence=0.35,
                    rationale="Trims into vol spikes — fewer whipsaws",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.006"),
                    confidence=0.3,
                    rationale="Bear vol spikes → small position, less pain",
                )
