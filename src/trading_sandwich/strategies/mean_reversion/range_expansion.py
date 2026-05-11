"""A8 Range Expansion/Contraction — Phase 3 Wave 1 Task 2.8.

Inverse-vol position sizing. Holds a target position that shrinks as
volatility expands and grows as volatility contracts — "buy the calm,
sell the storm". Scales the existing position toward that target, but
only when the gap exceeds a rebalance band, so it doesn't churn every
tick.

Target sizing from ATR percentile (0..100):

    target = base_size_usd * (100 - atr_percentile) / 50
    then clamped to [min_size_usd, max_size_usd]

  → pct 0   : 2 * base       (deep calm — max conviction)
  → pct 50  : 1 * base       (neutral)
  → pct 100 : 0 → min floor  (extreme vol — minimum exposure)

Action each tick:

    delta = target - position
    |delta| <= rebalance_band_pct * base_size_usd → no-op
    delta > 0 → buy delta at mid (entry, scale-in)
    delta < 0 → sell |delta| at mid (exit, scale-out)

Halal-spot inviolable: side='long' on every emitted intent. A scale-
out only ever reduces an existing long; never opens a short. (Since
delta < 0 means target < position and target >= min_size_usd >= 0,
|delta| < position always.)

Snapshot contract: {'mid_price', 'atr_percentile'} where
atr_percentile ∈ [0, 100]. The features stack already produces
atr_percentile_100; snapshot plumbing is the supporting task.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET]. Earns from vol
regime shifts — scaling into the calm before an expansion, out of
the chop before a contraction.

Structurally distinct from the discrete-bucket mean-reversion family
(A5/A6/A7): continuous sizing, the rebalance band is its own
hysteresis, no shared signal helper applies.
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


_COID_PREFIX = "rangex"


def _read_params(
    params: dict[str, Any],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    try:
        base = Decimal(str(params["base_size_usd"]))
        min_size = Decimal(str(params["min_size_usd"]))
        max_size = Decimal(str(params["max_size_usd"]))
        band_pct = Decimal(str(params["rebalance_band_pct"]))
    except KeyError as e:
        raise KeyError(
            f"range_expansion_contraction params missing required key: {e}"
        ) from e
    if min_size > max_size:
        raise ValueError(
            f"min_size_usd ({min_size}) must be <= max_size_usd ({max_size})"
        )
    return base, min_size, max_size, band_pct


def _target_size(
    atr_percentile: Decimal,
    base: Decimal,
    min_size: Decimal,
    max_size: Decimal,
) -> Decimal:
    raw = base * (Decimal("100") - atr_percentile) / Decimal("50")
    if raw < min_size:
        return min_size
    if raw > max_size:
        return max_size
    return raw


class RangeExpansionStrategy(Strategy):
    """A8 Range Expansion/Contraction — inverse-vol position sizing."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "atr_percentile"):
            if k not in snapshot:
                raise KeyError(
                    f"range_expansion_contraction requires snapshot[{k!r}]"
                )
        mid = Decimal(str(snapshot["mid_price"]))
        atr_percentile = Decimal(str(snapshot["atr_percentile"]))
        if atr_percentile < Decimal("0") or atr_percentile > Decimal("100"):
            raise ValueError(
                f"atr_percentile must be in [0, 100], got {atr_percentile}"
            )
        base, min_size, max_size, band_pct = _read_params(ctx.params)

        position = Decimal(ctx.state.get("position_size_usd", "0"))
        target = _target_size(atr_percentile, base, min_size, max_size)
        delta = target - position
        band = band_pct * base

        if abs(delta) <= band:
            ctx.state["position_size_usd"] = str(position)
            return []

        intents: list[OrderIntent] = []
        if delta > Decimal("0"):
            tick_idx = int(ctx.state.get("entry_count", 0))
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=delta,
                limit_price=mid,
                client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-entry-{tick_idx}",
                role="entry",
            ))
            ctx.state["entry_count"] = tick_idx + 1
        else:
            tick_idx = int(ctx.state.get("exit_count", 0))
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=-delta,
                limit_price=mid,
                client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-exit-{tick_idx}",
                role="exit",
                direction="sell",
            ))
            ctx.state["exit_count"] = tick_idx + 1

        ctx.state["position_size_usd"] = str(target)
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET].
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.025"),
                    confidence=0.5,
                    rationale="Vol regime shifts give scale-in/out edge",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.45,
                    rationale="Calm holds bigger position, slow drift up",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: trends dominate sizing logic",
                )
