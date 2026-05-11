"""Shared helpers for the mean-reversion family (A5/A6/A7 + future).

After three implementations of the same classify-into-buckets +
hysteresis + position-track pattern (A5 RSI, A6 Bollinger, A7
Z-Score), the duplication is load-bearing enough to lift.

Each strategy still owns its own classify step — the inputs differ
(RSI scalar / Bollinger bands / z-score) and the comparison logic
is small enough that abstracting it would create more friction than
it removes. What's identical across all three is what comes AFTER
classification: hysteresis, position tracking, and order emission.

The helper preserves each strategy's chosen kind names ('oversold'/
'overbought'/'neutral' for RSI, 'lower'/'upper'/'middle' for
Bollinger, 'low'/'high'/'middle' for Z-Score) so existing tests stay
intact. The strategy passes the three name strings explicitly.
"""
from __future__ import annotations

from decimal import Decimal

from trading_sandwich.strategies.base import OrderIntent, StrategyContext


def apply_signal(
    *,
    ctx: StrategyContext,
    kind: str,
    entry_kind_name: str,
    exit_kind_name: str,
    mid: Decimal,
    entry_size: Decimal,
    coid_prefix: str,
) -> list[OrderIntent]:
    """Apply hysteresis + emit entry/exit OrderIntents based on kind
    transition. Mutates ctx.state['last_signal_kind'] and
    ctx.state['position_size_usd'].

    On transition into entry_kind_name → emit one buy at mid (entry).
    On transition into exit_kind_name AND position>0 → emit one sell
    at mid sized to position (exit).
    Otherwise → no intent.

    Halal-spot inviolable: side='long' on every emitted OrderIntent.
    Exits only fire when position > 0; the strategy never opens a
    short.
    """
    last_kind = ctx.state.get("last_signal_kind")
    position = Decimal(ctx.state.get("position_size_usd", "0"))

    if kind == last_kind:
        ctx.state["last_signal_kind"] = kind
        ctx.state["position_size_usd"] = str(position)
        return []

    intents: list[OrderIntent] = []
    if kind == entry_kind_name:
        tick_idx = int(ctx.state.get("entry_count", 0))
        intents.append(OrderIntent(
            symbol=ctx.symbol,
            order_type="limit",
            size_usd=entry_size,
            limit_price=mid,
            client_order_id=f"{coid_prefix}-{ctx.strategy_id}-entry-{tick_idx}",
            role="entry",
        ))
        position += entry_size
        ctx.state["entry_count"] = tick_idx + 1
    elif kind == exit_kind_name and position > Decimal("0"):
        tick_idx = int(ctx.state.get("exit_count", 0))
        intents.append(OrderIntent(
            symbol=ctx.symbol,
            order_type="limit",
            size_usd=position,
            limit_price=mid,
            client_order_id=f"{coid_prefix}-{ctx.strategy_id}-exit-{tick_idx}",
            role="exit",
        ))
        position = Decimal("0")
        ctx.state["exit_count"] = tick_idx + 1

    ctx.state["last_signal_kind"] = kind
    ctx.state["position_size_usd"] = str(position)
    return intents
