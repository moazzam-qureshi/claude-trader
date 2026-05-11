"""B4 Indicator-Triggered DCA — Phase 3 Wave 1 Task 2.12.

Fixed-dollar accumulation, but the trigger is an RSI threshold breach
rather than a calendar interval. The strategy fires a buy when:

    rsi < rsi_threshold  AND  cooldown_seconds elapsed since last fire

The cooldown stops it from buying on every 30s tick while RSI sits
below the line — "RSI<30 daily" means at most one fire per day.

Halal-spot inviolable: every emitted intent has side='long', a market
buy with role='entry'. Accumulation only — no overbought exit, never
sells. (That distinguishes it from A5 RSI Mean Reversion, which does
sell on RSI>70.)

Snapshot contract: {'now': datetime (tz-aware), 'rsi': Decimal}. The
features stack already produces rsi_14; snapshot plumbing is the
supporting task.

State: last_buy_at (iso), buy_count, total_contributed_usd.

Spec §6.2 compat: [TREND_DOWN, RANGE_VOLATILE] — the regimes where
RSI<30 events actually happen.
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


_COID_PREFIX = "dcaind"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, int]:
    try:
        contribution = Decimal(str(params["contribution_usd"]))
        rsi_threshold = Decimal(str(params["rsi_threshold"]))
        cooldown = int(params["cooldown_seconds"])
    except KeyError as e:
        raise KeyError(
            f"dca_indicator params missing required key: {e}"
        ) from e
    if contribution <= Decimal("0"):
        raise ValueError(f"contribution_usd must be > 0, got {contribution}")
    if cooldown <= 0:
        raise ValueError(f"cooldown_seconds must be > 0, got {cooldown}")
    if rsi_threshold <= Decimal("0") or rsi_threshold >= Decimal("100"):
        raise ValueError(
            f"rsi_threshold must be in (0, 100), got {rsi_threshold}"
        )
    return contribution, rsi_threshold, cooldown


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class IndicatorTriggeredDcaStrategy(Strategy):
    """B4 Indicator-Triggered DCA — fires on RSI<threshold + cooldown."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "rsi"):
            if k not in snapshot:
                raise KeyError(f"dca_indicator requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        rsi = Decimal(str(snapshot["rsi"]))
        contribution, rsi_threshold, cooldown = _read_params(ctx.params)

        if rsi >= rsi_threshold:
            return []

        last_buy_at_iso = ctx.state.get("last_buy_at")
        if last_buy_at_iso is not None:
            last_buy_at = datetime.fromisoformat(last_buy_at_iso)
            if (now - last_buy_at).total_seconds() < cooldown:
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
        # Spec §6.2 compat: [TREND_DOWN, RANGE_VOLATILE].
        match regime:
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.022"),
                    confidence=0.45,
                    rationale="Bears flush RSI<30 repeatedly → cheap fills",
                )
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.016"),
                    confidence=0.4,
                    rationale="Choppy lows touch RSI<30 occasionally",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.6,
                    rationale="Uptrend/quiet rarely hits RSI<30 → no fires",
                )
