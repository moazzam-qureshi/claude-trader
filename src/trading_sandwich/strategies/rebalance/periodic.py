"""C1 Periodic Rebalancing — Phase 3 Wave 1 Task 2.14.

Single-symbol periodic rebalancing: hold this symbol's position value
at target_fraction of the strategy's allocated capital, resetting on a
calendar interval. The "portfolio" being balanced is {this asset, cash}.

  target_value = target_fraction * capital_allocated_usd
  then close the gap to actual position value (see rebalance/_base.py).

Interval gating + no-catch-up after worker downtime: same pattern as
the DCA family. First tick rebalances immediately (establishes the
position). The rebalance "completes" each interval even when the
position was already on target — the count still advances.

Halal-spot inviolable: side='long' on every intent. A sell only
reduces an existing long; sell value capped at the held value.

Position units are estimated as size_usd / mid on a buy (near-mid
fill assumption); fill-delivery plumbing will correct with the real
fill quantity later.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.
State: position_units, rebalance_count, last_rebalance_at (iso).

Spec §6.2 compat: ["*"] — universal.
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
from trading_sandwich.strategies.rebalance._base import rebalance_toward_value


_COID_PREFIX = "rebper"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, int]:
    try:
        target_fraction = Decimal(str(params["target_fraction"]))
        interval = int(params["interval_seconds"])
    except KeyError as e:
        raise KeyError(
            f"rebalance_periodic params missing required key: {e}"
        ) from e
    if target_fraction <= Decimal("0") or target_fraction > Decimal("1"):
        raise ValueError(
            f"target_fraction must be in (0, 1], got {target_fraction}"
        )
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    return target_fraction, interval


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class PeriodicRebalanceStrategy(Strategy):
    """C1 Periodic Rebalancing — calendar reset to a target fraction."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "mid_price"):
            if k not in snapshot:
                raise KeyError(f"rebalance_periodic requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        mid = Decimal(str(snapshot["mid_price"]))
        target_fraction, interval = _read_params(ctx.params)

        last_iso = ctx.state.get("last_rebalance_at")
        if last_iso is not None:
            last_at = datetime.fromisoformat(last_iso)
            if (now - last_at).total_seconds() < interval:
                return []

        count = int(ctx.state.get("rebalance_count", 0))
        units = Decimal(ctx.state.get("position_units", "0"))
        target_value = target_fraction * ctx.capital_allocated_usd

        intents, new_units = rebalance_toward_value(
            ctx=ctx, target_value=target_value, mid=mid, units=units,
            coid_prefix=_COID_PREFIX, seq=count,
        )

        ctx.state["rebalance_count"] = count + 1
        ctx.state["position_units"] = str(new_units)
        ctx.state["last_rebalance_at"] = now.isoformat()
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: ["*"]. Rebalancing harvests volatility.
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.02"),
                    confidence=0.45,
                    rationale="Chop = sell-high/buy-low around the target",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.01"),
                    confidence=0.4,
                    rationale="Less drift, smaller rebalance harvest",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.008"),
                    confidence=0.4,
                    rationale="Trims winners (drag) but keeps risk capped",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.006"),
                    confidence=0.35,
                    rationale="Buys into weakness on schedule",
                )
