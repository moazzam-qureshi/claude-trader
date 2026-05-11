"""B2 Value Averaging — Phase 3 Wave 1 Task 2.10.

Classic value averaging. A linear value path sets, for each interval,
what the position's market value *should* be:

    target_value(n) = base_growth_usd * (n + 1)

At each interval the strategy buys (or sells) the gap between the
target value and the actual position value:

    delta_usd = target_value(interval_count) - position_units * mid
    delta > 0 → buy delta worth at mid (entry)
    delta < 0 → sell |delta| worth at mid (exit, capped at position)

So a market that ran ahead of the path → smaller buy, or even a sell;
a market that lagged → bigger buy. The path, not a fixed cadence
amount, drives the contribution.

Position units are estimated as size_usd / mid when a buy is emitted
(assumes a near-mid fill). When fill-delivery plumbing lands the
worker will overwrite units with the real fill quantity; until then
the estimate is the contract — backtest uses real fills, live needs
the supporting task.

Interval gating identical to B1 Calendar DCA: first tick fires
immediately, subsequent only after interval_seconds elapsed; no
catch-up after worker downtime.

Halal-spot inviolable: every emitted intent has side='long'. A sell
only ever reduces an existing long; sell value is capped at the
held position value so it can never go short.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.
State: position_units, interval_count, total_contributed_usd,
last_action_at (iso).

Spec §6.2 compat: ["*"]. Best in ranging markets — the buy-low/
sell-high oscillation around the path needs chop to pay.
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


_COID_PREFIX = "dcava"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, int]:
    try:
        base_growth = Decimal(str(params["base_growth_usd"]))
        interval = int(params["interval_seconds"])
    except KeyError as e:
        raise KeyError(
            f"dca_value_averaging params missing required key: {e}"
        ) from e
    if base_growth <= Decimal("0"):
        raise ValueError(f"base_growth_usd must be > 0, got {base_growth}")
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    return base_growth, interval


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class ValueAveragingStrategy(Strategy):
    """B2 Value Averaging — buy/sell toward a target value path."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "mid_price"):
            if k not in snapshot:
                raise KeyError(f"dca_value_averaging requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        mid = Decimal(str(snapshot["mid_price"]))
        base_growth, interval = _read_params(ctx.params)

        last_action_iso = ctx.state.get("last_action_at")
        if last_action_iso is not None:
            last_action_at = datetime.fromisoformat(last_action_iso)
            if (now - last_action_at).total_seconds() < interval:
                return []

        interval_count = int(ctx.state.get("interval_count", 0))
        units = Decimal(ctx.state.get("position_units", "0"))
        total = Decimal(ctx.state.get("total_contributed_usd", "0"))

        target_value = base_growth * Decimal(interval_count + 1)
        actual_value = units * mid
        delta = target_value - actual_value

        intents: list[OrderIntent] = []
        if delta > Decimal("0"):
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=delta,
                limit_price=mid,
                client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-entry-{interval_count}",
                role="entry",
            ))
            units += delta / mid
            total += delta
        elif delta < Decimal("0"):
            sell_value = min(-delta, actual_value)
            if sell_value > Decimal("0"):
                intents.append(OrderIntent(
                    symbol=ctx.symbol,
                    order_type="limit",
                    size_usd=sell_value,
                    limit_price=mid,
                    client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-exit-{interval_count}",
                    role="exit",
                ))
                units -= sell_value / mid
                if units < Decimal("0"):
                    units = Decimal("0")

        ctx.state["interval_count"] = interval_count + 1
        ctx.state["position_units"] = str(units)
        ctx.state["total_contributed_usd"] = str(total)
        ctx.state["last_action_at"] = now.isoformat()
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec: ranging markets are value averaging's home.
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.025"),
                    confidence=0.45,
                    rationale="Chop around the path = buy low / sell high",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.015"),
                    confidence=0.4,
                    rationale="Less oscillation, smaller harvest",
                )
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.4,
                    rationale="Path forces bigger buys into weakness",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.008"),
                    confidence=0.35,
                    rationale="Uptrend: path is satisfied with small buys",
                )
