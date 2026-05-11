"""Shared helper for the trend family (D1/D2/...).

Every binary in/out trend follower runs the same plumbing: hold a
boolean position state, buy to a fixed dollar size when the entry
signal fires (and we're flat), sell the whole position when the exit
signal fires (and we're long), and do nothing otherwise. What differs
between strategies is only how the entry/exit signals are computed.

Lifted out after the second binary trend strategy (D1 MA Crossover,
D2 Donchian Breakout). D3/D4/D5 reuse it too.

The strategy passes two booleans:
  enter_signal — the entry condition fired this tick
  exit_signal  — the exit condition fired this tick
Between-band "hold" is naturally expressed as both False; a strategy
where exactly one is always true (like an MA crossover) just sets
exit_signal = not enter_signal.

State keys touched: in_position (bool), position_units (str),
entry_count (int), exit_count (int).
"""
from __future__ import annotations

from decimal import Decimal

from trading_sandwich.strategies.base import OrderIntent, StrategyContext


def apply_binary_trend_signal(
    *,
    ctx: StrategyContext,
    enter_signal: bool,
    exit_signal: bool,
    position_usd: Decimal,
    mid: Decimal,
    coid_prefix: str,
) -> list[OrderIntent]:
    """Manage the binary position. Mutates ctx.state's in_position,
    position_units, entry_count, exit_count.

    enter_signal and not in_position → buy position_usd at mid (entry)
    exit_signal and in_position      → sell the whole position at mid (exit)
    otherwise                        → no intent

    Halal-spot inviolable: side='long' on every emitted OrderIntent;
    the exit sells the held position to cash, never opens a short.
    Position units are estimated as size_usd / mid on entry.
    """
    in_position = bool(ctx.state.get("in_position", False))
    units = Decimal(ctx.state.get("position_units", "0"))
    entry_count = int(ctx.state.get("entry_count", 0))
    exit_count = int(ctx.state.get("exit_count", 0))

    intents: list[OrderIntent] = []
    if enter_signal and not in_position:
        intents.append(OrderIntent(
            symbol=ctx.symbol,
            order_type="limit",
            size_usd=position_usd,
            limit_price=mid,
            client_order_id=f"{coid_prefix}-{ctx.strategy_id}-entry-{entry_count}",
            role="entry",
        ))
        units = position_usd / mid
        in_position = True
        entry_count += 1
    elif exit_signal and in_position:
        exit_value = units * mid
        if exit_value > Decimal("0"):
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=exit_value,
                limit_price=mid,
                client_order_id=f"{coid_prefix}-{ctx.strategy_id}-exit-{exit_count}",
                role="exit",
                direction="sell",
            ))
            exit_count += 1
        units = Decimal("0")
        in_position = False

    ctx.state["in_position"] = in_position
    ctx.state["position_units"] = str(units)
    ctx.state["entry_count"] = entry_count
    ctx.state["exit_count"] = exit_count
    return intents
