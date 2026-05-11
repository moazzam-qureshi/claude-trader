"""B3 Volatility-Adjusted DCA — Phase 3 Wave 1 Task 2.11.

Calendar-cadence accumulation like B1, but the contribution scales up
with volatility — bigger buys when the market is shaky (which usually
means cheaper assets):

    contribution = base * (1 + (vol_multiplier_max - 1) * atr_pct/100)
    → atr_pct 0   : base       (the floor — never DCA less than base)
    → atr_pct 100 : base * vol_multiplier_max
    linear in between.

Interval gating + no-catch-up after worker downtime: identical to B1
Calendar DCA. First tick fires immediately.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. DCA only ever accumulates.

Snapshot contract: {'now': datetime (tz-aware), 'atr_percentile':
Decimal ∈ [0, 100]}. The features stack already produces
atr_percentile_100; snapshot plumbing is the supporting task.

State: last_buy_at (iso), buy_count, total_contributed_usd.

Spec §6.2 compat: ["*"]. Best in bear markets.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)


_COID_PREFIX = "dcavol"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, int, Decimal]:
    try:
        base = Decimal(str(params["base_contribution_usd"]))
        interval = int(params["interval_seconds"])
        vol_max = Decimal(str(params["vol_multiplier_max"]))
    except KeyError as e:
        raise KeyError(
            f"dca_volatility_adj params missing required key: {e}"
        ) from e
    if base <= Decimal("0"):
        raise ValueError(f"base_contribution_usd must be > 0, got {base}")
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    if vol_max < Decimal("1"):
        raise ValueError(
            f"vol_multiplier_max must be >= 1 (vol can only boost, not "
            f"shrink, contributions); got {vol_max}"
        )
    return base, interval, vol_max


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


def _scaled_contribution(
    base: Decimal, vol_max: Decimal, atr_percentile: Decimal,
) -> Decimal:
    factor = Decimal("1") + (vol_max - Decimal("1")) * atr_percentile / Decimal("100")
    return base * factor


class VolatilityAdjustedDcaStrategy(Strategy):
    """B3 Volatility-Adjusted DCA — contribution scales with vol."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "atr_percentile"):
            if k not in snapshot:
                raise KeyError(f"dca_volatility_adj requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        atr_percentile = Decimal(str(snapshot["atr_percentile"]))
        if atr_percentile < Decimal("0") or atr_percentile > Decimal("100"):
            raise ValueError(
                f"atr_percentile must be in [0, 100], got {atr_percentile}"
            )
        base, interval, vol_max = _read_params(ctx.params)

        last_buy_at_iso = ctx.state.get("last_buy_at")
        if last_buy_at_iso is not None:
            last_buy_at = datetime.fromisoformat(last_buy_at_iso)
            if (now - last_buy_at).total_seconds() < interval:
                return []

        buy_count = int(ctx.state.get("buy_count", 0))
        total = Decimal(ctx.state.get("total_contributed_usd", "0"))
        contribution = _scaled_contribution(base, vol_max, atr_percentile)

        intent = OrderIntent(
            symbol=ctx.symbol,
            order_type="market",
            size_usd=contribution,
            client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-buy-{buy_count}",
            role="entry",
        )
        ctx.state["last_buy_at"] = now.isoformat()
        ctx.state["buy_count"] = buy_count + 1
        ctx.state["total_contributed_usd"] = str(total + contribution)
        return [intent]

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec: bear markets. Vol-up buys cheaper assets.
        match regime:
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.025"),
                    confidence=0.45,
                    rationale="Vol spikes in bears → bigger buys at lows",
                )
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.018"),
                    confidence=0.4,
                    rationale="Choppy vol → above-base contributions",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.4,
                    rationale="Calmer uptrend → near-base contributions",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.01"),
                    confidence=0.35,
                    rationale="Quiet range → base contributions",
                )
