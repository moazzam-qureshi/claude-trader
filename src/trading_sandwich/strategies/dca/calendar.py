"""B1 Calendar DCA — Phase 3 Wave 1 Task 2.9.

Fixed-dollar market buy every `interval_seconds`. First tick fires
immediately (no prior buy). Subsequent ticks fire only after the
interval has elapsed since the last recorded buy.

If the worker was down for several intervals, the next tick fires
exactly ONE buy (no catch-up) and resumes the cadence from now —
over-deploying after an outage is worse than skipping.

Snapshot contract: {'now': datetime (timezone-aware)}. The worker
will inject datetime.now(timezone.utc) once snapshot plumbing lands;
until then the integration test injects.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. DCA only ever accumulates — never sells.

State: {'last_buy_at': iso str, 'buy_count': int,
'total_contributed_usd': decimal str}.

Spec §6.2 compat: ["*"] — universal accumulation.
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


_COID_PREFIX = "dcacal"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, int]:
    try:
        contribution = Decimal(str(params["contribution_usd"]))
        interval = int(params["interval_seconds"])
    except KeyError as e:
        raise KeyError(f"dca_calendar params missing required key: {e}") from e
    if contribution <= Decimal("0"):
        raise ValueError(f"contribution_usd must be > 0, got {contribution}")
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    return contribution, interval


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class CalendarDcaStrategy(Strategy):
    """B1 Calendar DCA — fixed $X every interval."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "now" not in snapshot:
            raise KeyError("dca_calendar requires snapshot['now']")
        now = _require_aware(snapshot["now"])
        contribution, interval = _read_params(ctx.params)

        last_buy_at_iso = ctx.state.get("last_buy_at")
        if last_buy_at_iso is not None:
            last_buy_at = datetime.fromisoformat(last_buy_at_iso)
            elapsed = (now - last_buy_at).total_seconds()
            if elapsed < interval:
                return []

        buy_count = int(ctx.state.get("buy_count", 0))
        total = Decimal(ctx.state.get("total_contributed_usd", "0"))

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
        # Spec §6.2 compat: ["*"]. DCA accumulates everywhere; buying
        # into a downtrend gets the best average cost.
        match regime:
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.02"),
                    confidence=0.4,
                    rationale="Cost-averaging shines buying weakness",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.015"),
                    confidence=0.4,
                    rationale="Still accumulating, just at higher prices",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.4,
                    rationale="Steady accumulation through chop",
                )
