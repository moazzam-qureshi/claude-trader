"""C3 Risk Parity — Phase 3 Wave 1 Task 2.16.

Single-symbol risk parity: scale the target position inversely to the
symbol's volatility so the dollar-risk (position_value * atr_pct) is
held constant at target_risk_pct of allocated capital.

  target_value = target_risk_pct * capital_allocated_usd / atr_pct
  then clamped to [0, max_fraction * capital_allocated_usd]
  then close the gap to actual position value (rebalance/_base.py)

  → low vol  → large position
  → high vol → small position
  product target_value * atr_pct ≈ target_risk_pct * capital  (constant)

In a multi-asset portfolio this would equalise risk *across* assets;
here, deployed per symbol, it equalises risk *over time* — leaning in
when the asset is calm, trimming when it's wild.

Calendar-cadence rebalance like C1: first tick rebalances immediately,
subsequent only after interval_seconds; no catch-up after downtime.

Halal-spot inviolable: side='long' on every intent. Sell value
capped at the held value — never goes short. Position units estimated
as size_usd / mid on a buy; fill-delivery plumbing corrects later.
Reuses rebalance/_base.py's rebalance_toward_value().

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal,
'atr_pct': Decimal} where atr_pct = ATR / price (a small fraction,
typically 0.005..0.10). The features stack has atr_14 and close; the
supporting task feeds atr_14 / close as atr_pct. State: position_units,
rebalance_count, last_rebalance_at (iso).

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


_COID_PREFIX = "rebrp"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, int, Decimal]:
    try:
        target_risk = Decimal(str(params["target_risk_pct"]))
        interval = int(params["interval_seconds"])
        max_fraction = Decimal(str(params["max_fraction"]))
    except KeyError as e:
        raise KeyError(
            f"rebalance_risk_parity params missing required key: {e}"
        ) from e
    if target_risk <= Decimal("0"):
        raise ValueError(f"target_risk_pct must be > 0, got {target_risk}")
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    if max_fraction <= Decimal("0") or max_fraction > Decimal("1"):
        raise ValueError(f"max_fraction must be in (0, 1], got {max_fraction}")
    return target_risk, interval, max_fraction


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class RiskParityStrategy(Strategy):
    """C3 Risk Parity — vol-weighted position sizing on a cadence."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "mid_price", "atr_pct"):
            if k not in snapshot:
                raise KeyError(f"rebalance_risk_parity requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        mid = Decimal(str(snapshot["mid_price"]))
        atr_pct = Decimal(str(snapshot["atr_pct"]))
        if atr_pct <= Decimal("0"):
            raise ValueError(f"atr_pct must be > 0, got {atr_pct}")
        target_risk, interval, max_fraction = _read_params(ctx.params)

        last_iso = ctx.state.get("last_rebalance_at")
        if last_iso is not None:
            last_at = datetime.fromisoformat(last_iso)
            if (now - last_at).total_seconds() < interval:
                return []

        count = int(ctx.state.get("rebalance_count", 0))
        units = Decimal(ctx.state.get("position_units", "0"))

        capital = ctx.capital_allocated_usd
        raw_target = target_risk * capital / atr_pct
        cap = max_fraction * capital
        target_value = min(raw_target, cap)

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
        # Spec §6.2 compat: ["*"]. Smoother ride: caps drawdowns in vol
        # spikes, leans in when calm.
        match regime:
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.014"),
                    confidence=0.4,
                    rationale="Calm → bigger position, captures the drift",
                )
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.01"),
                    confidence=0.4,
                    rationale="Trims into vol spikes — fewer whipsaws",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.4,
                    rationale="Uptrends are often lower-vol → larger size",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.006"),
                    confidence=0.35,
                    rationale="Bear vol spikes → small position, less pain",
                )
