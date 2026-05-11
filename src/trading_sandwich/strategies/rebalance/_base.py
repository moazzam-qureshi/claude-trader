"""Shared helper for the rebalancing family (C1/C2/...).

Single-symbol rebalancing always reduces to the same move: given a
target dollar value for the position and the current units, emit a
buy or sell to close the gap, capping a sell at the held value so we
never go short.

Lifted out as soon as the second rebalance variant needed it
(C2 Threshold Rebalancing reuses the exact gap-closing logic; only
the *trigger* differs — calendar vs drift). C3 Risk Parity computes a
different target but still closes the gap the same way, so it reuses
this too.
"""
from __future__ import annotations

from decimal import Decimal

from trading_sandwich.strategies.base import OrderIntent, StrategyContext


def rebalance_toward_value(
    *,
    ctx: StrategyContext,
    target_value: Decimal,
    mid: Decimal,
    units: Decimal,
    coid_prefix: str,
    seq: int,
) -> tuple[list[OrderIntent], Decimal]:
    """Emit at most one OrderIntent moving the position toward
    target_value, and return (intents, new_units).

    delta = target_value - units * mid
    delta > 0 → buy delta worth at mid (role='rebalance')
    delta < 0 → sell min(|delta|, units*mid) worth at mid (role='rebalance')
    delta == 0 → no intent

    Halal-spot inviolable: side='long' on every intent; the sell
    branch caps at the held value so units can never go negative.
    """
    actual_value = units * mid
    delta = target_value - actual_value
    if delta == Decimal("0"):
        return [], units

    if delta > Decimal("0"):
        intent = OrderIntent(
            symbol=ctx.symbol,
            order_type="limit",
            size_usd=delta,
            limit_price=mid,
            client_order_id=f"{coid_prefix}-{ctx.strategy_id}-rb{seq}",
            role="rebalance",
        )
        return [intent], units + delta / mid

    sell_value = min(-delta, actual_value)
    if sell_value <= Decimal("0"):
        return [], units
    intent = OrderIntent(
        symbol=ctx.symbol,
        order_type="limit",
        size_usd=sell_value,
        limit_price=mid,
        client_order_id=f"{coid_prefix}-{ctx.strategy_id}-rb{seq}",
        role="rebalance",
        direction="sell",
    )
    new_units = units - sell_value / mid
    if new_units < Decimal("0"):
        new_units = Decimal("0")
    return [intent], new_units
