"""Shared helpers for the grid family (A1/A2/A3 + later A4).

Lifted out of standard.py / infinity.py / geometric.py once three
grid variants made the duplication clearly load-bearing. Pure
behavior-preserving refactor: each helper does exactly what its
caller used to do inline, with no semantic changes.

The helpers operate on the canonical per-rung state schema:

    {"price": str, "side": "buy", "submitted": bool,
     "filled_buy": bool, "submitted_sell": bool,
     "client_order_id": str}

Strategies pass a `coid_prefix` (e.g. 'gridstd', 'gridinf', 'gridgeo')
so client_order_ids stay distinguishable across strategy types when
they appear together in execution logs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_sandwich.strategies.base import OrderIntent, StrategyContext


def deploy_buy_ladder(
    *,
    ctx: StrategyContext,
    prices: list[Decimal],
    mid: Decimal,
    size_per_level: Decimal,
    coid_prefix: str,
) -> tuple[list[OrderIntent], list[dict[str, Any]]]:
    """Build the per-rung state list and the initial buy intents.

    Submits a buy LIMIT at every rung whose price <= mid; records
    every rung in state regardless (so a later tick where price
    re-enters the range can submit then).

    Returns (intents, levels_state). The caller is responsible for
    writing levels_state into ctx.state.
    """
    intents: list[OrderIntent] = []
    levels_state: list[dict[str, Any]] = []
    for i, price in enumerate(prices):
        should_submit = price <= mid
        coid = f"{coid_prefix}-{ctx.strategy_id}-L{i}-entry"
        if should_submit:
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=size_per_level,
                limit_price=price,
                client_order_id=coid,
                role="entry",
                grid_level=i,
            ))
        levels_state.append({
            "price": str(price),
            "side": "buy",
            "submitted": should_submit,
            "filled_buy": False,
            "submitted_sell": False,
            "client_order_id": coid,
        })
    return intents, levels_state


def emit_sells_for_fills(
    *,
    ctx: StrategyContext,
    size_per_level: Decimal,
    coid_prefix: str,
) -> list[OrderIntent]:
    """For every rung whose buy has filled but whose paired sell hasn't
    been submitted yet, emit a sell at the next-higher rung's price.
    Mutates state in place: marks submitted_sell=True on each rung that
    contributes a sell, so the next tick is idempotent.

    The top rung has no rung above it; a fill there is recorded but
    emits no sell (caller's responsibility to interpret — usually means
    accept the inventory and let the next bar's price action drive).
    """
    levels = ctx.state["levels"]
    intents: list[OrderIntent] = []
    for i, lvl in enumerate(levels):
        if not lvl.get("filled_buy"):
            continue
        if lvl.get("submitted_sell"):
            continue
        if i + 1 >= len(levels):
            continue
        sell_price = Decimal(levels[i + 1]["price"])
        sell_coid = f"{coid_prefix}-{ctx.strategy_id}-L{i + 1}-exit"
        intents.append(OrderIntent(
            symbol=ctx.symbol,
            order_type="limit",
            size_usd=size_per_level,
            limit_price=sell_price,
            client_order_id=sell_coid,
            role="exit",
            direction="sell",
            grid_level=i + 1,
        ))
        lvl["submitted_sell"] = True
    return intents
