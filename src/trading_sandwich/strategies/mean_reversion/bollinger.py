"""A6 Bollinger Reversion — Phase 3 Wave 1 Task 2.6.

Mechanic: read mid_price + Bollinger upper/lower bands from snapshot.
  mid <= bb_lower → emit one buy at mid (entry).
  mid >= bb_upper AND position > 0 → emit one sell at mid (exit)
    sized to current position.

Hysteresis: last_signal_kind ∈ {'lower', 'upper', 'middle'} dedupes
consecutive same-kind reads; the strategy fires only on transition
into a new bucket.

Halal-spot inviolable: every emitted intent has side='long'. Sells
only happen when position > 0.

Snapshot contract: {'mid_price', 'bb_lower', 'bb_upper'}. The
existing features stack already produces bb_upper / bb_middle /
bb_lower from compute.py; snapshot plumbing is the supporting task.

Bollinger bands themselves are computed at fixed period=20, std=2 in
features/compute.py. A6 doesn't reconfigure them; it consumes the
output. Different bands (e.g. 50/2.5) would mean a separate strategy.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET]. Best in stable vol.

Structurally similar to A5 RSI Mean Reversion: classify-into-buckets +
hysteresis. After A7 (Z-Score), three implementations of the same
pattern will justify a shared mean-reversion helper.
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


_COID_PREFIX = "bb"


def _read_params(params: dict[str, Any]) -> Decimal:
    try:
        return Decimal(str(params["entry_size_usd"]))
    except KeyError as e:
        raise KeyError(
            f"bollinger_reversion params missing required key: {e}"
        ) from e


def _classify(
    mid: Decimal, bb_lower: Decimal, bb_upper: Decimal,
) -> str:
    if mid <= bb_lower:
        return "lower"
    if mid >= bb_upper:
        return "upper"
    return "middle"


class BollingerReversionStrategy(Strategy):
    """A6 Bollinger Reversion."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "bb_lower", "bb_upper"):
            if k not in snapshot:
                raise KeyError(f"bollinger_reversion requires snapshot[{k!r}]")
        mid = Decimal(str(snapshot["mid_price"]))
        bb_lower = Decimal(str(snapshot["bb_lower"]))
        bb_upper = Decimal(str(snapshot["bb_upper"]))
        entry_size = _read_params(ctx.params)

        kind = _classify(mid, bb_lower, bb_upper)
        last_kind = ctx.state.get("last_signal_kind")
        position = Decimal(ctx.state.get("position_size_usd", "0"))

        if kind == last_kind:
            ctx.state["last_signal_kind"] = kind
            ctx.state["position_size_usd"] = str(position)
            return []

        intents: list[OrderIntent] = []
        if kind == "lower":
            tick_idx = int(ctx.state.get("entry_count", 0))
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=entry_size,
                limit_price=mid,
                client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-entry-{tick_idx}",
                role="entry",
            ))
            position += entry_size
            ctx.state["entry_count"] = tick_idx + 1
        elif kind == "upper" and position > Decimal("0"):
            tick_idx = int(ctx.state.get("exit_count", 0))
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=position,
                limit_price=mid,
                client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-exit-{tick_idx}",
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
        # Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET].
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.035"),
                    confidence=0.55,
                    rationale="Band touches revert in choppy markets",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.018"),
                    confidence=0.5,
                    rationale="Tight bands, fewer signals, smaller harvest",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: bands ride trends",
                )
