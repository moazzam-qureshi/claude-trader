"""A5 RSI Mean Reversion — Phase 3 Wave 1 Task 2.5.

Mechanic: read RSI from snapshot.
  RSI < oversold_threshold (default 30): emit a buy at mid_price (entry).
  RSI > overbought_threshold (default 70): emit a sell at mid_price
    (exit) sized to whatever inventory we currently hold.

Hysteresis: at most one signal per breach event. After firing on
oversold, no more buys until RSI returns above oversold and then
re-breaches. Same logic for overbought sells. last_signal_kind in
state is the dedupe key.

Halal-spot inviolable: every emitted intent has side='long'. Sells
only happen when state['position_size_usd'] > 0 — the strategy never
opens a short.

Snapshot contract: {'mid_price': Decimal, 'rsi': Decimal}. Snapshot
plumbing (delivering the latest features.rsi_14 to the worker) is a
later Wave 1 supporting task.

Spec §6.2 compat: [RANGE_VOLATILE]. RSI mean-reversion only earns in
choppy regimes; trending markets stay overbought or oversold for
long stretches.
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


_COID_PREFIX = "rsi"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    try:
        oversold = Decimal(str(params["rsi_oversold"]))
        overbought = Decimal(str(params["rsi_overbought"]))
        entry_size = Decimal(str(params["entry_size_usd"]))
    except KeyError as e:
        raise KeyError(
            f"rsi_mean_reversion params missing required key: {e}"
        ) from e
    if oversold >= overbought:
        raise ValueError(
            f"rsi_oversold ({oversold}) must be < rsi_overbought ({overbought})"
        )
    return oversold, overbought, entry_size


def _classify(rsi: Decimal, oversold: Decimal, overbought: Decimal) -> str:
    if rsi < oversold:
        return "oversold"
    if rsi > overbought:
        return "overbought"
    return "neutral"


class RsiMeanReversionStrategy(Strategy):
    """A5 RSI Mean Reversion."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("rsi_mean_reversion requires snapshot['mid_price']")
        if "rsi" not in snapshot:
            raise KeyError("rsi_mean_reversion requires snapshot['rsi']")
        mid = Decimal(str(snapshot["mid_price"]))
        rsi = Decimal(str(snapshot["rsi"]))
        oversold, overbought, entry_size = _read_params(ctx.params)

        kind = _classify(rsi, oversold, overbought)
        last_kind = ctx.state.get("last_signal_kind")
        position = Decimal(ctx.state.get("position_size_usd", "0"))

        # Hysteresis: don't fire while we're stuck in the same regime as
        # last fire. last_kind is updated on EVERY tick so 'neutral' is
        # the natural reset.
        if kind == last_kind:
            ctx.state["last_signal_kind"] = kind
            ctx.state["position_size_usd"] = str(position)
            return []

        intents: list[OrderIntent] = []
        if kind == "oversold":
            tick_idx = int(ctx.state.get("entry_count", 0))
            coid = f"{_COID_PREFIX}-{ctx.strategy_id}-entry-{tick_idx}"
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=entry_size,
                limit_price=mid,
                client_order_id=coid,
                role="entry",
            ))
            position += entry_size
            ctx.state["entry_count"] = tick_idx + 1
        elif kind == "overbought" and position > Decimal("0"):
            tick_idx = int(ctx.state.get("exit_count", 0))
            coid = f"{_COID_PREFIX}-{ctx.strategy_id}-exit-{tick_idx}"
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=position,
                limit_price=mid,
                client_order_id=coid,
                role="exit",
            ))
            position = Decimal("0")
            ctx.state["exit_count"] = tick_idx + 1

        ctx.state["last_signal_kind"] = kind
        ctx.state["position_size_usd"] = str(position)
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # spec §6.2 compat: [RANGE_VOLATILE].
        if regime == Regime.RANGE_VOLATILE:
            return ReturnExpectation(
                monthly_return_pct=Decimal("0.04"),
                confidence=0.55,
                rationale="Choppy markets give RSI extremes that revert",
            )
        return ReturnExpectation(
            monthly_return_pct=Decimal("0"),
            confidence=0.7,
            rationale="Trending or quiet: RSI extremes don't revert",
        )
