"""D1 MA Crossover — Phase 3 Wave 1 Task 2.18.

Binary in/out trend follower: long the asset while the fast MA is
above the slow MA (golden-cross regime), all cash while it's at or
below (death-cross regime).

  ma_fast > ma_slow  and not in position → buy position_usd at mid (entry)
  ma_fast <= ma_slow and     in position → sell the whole position at mid (exit)
  otherwise → no-op

State is binary: in_position (bool) + position_units. Acting only on
the transition means a sustained golden cross emits one entry, not
one per tick.

Halal-spot inviolable: side='long' on every intent. The exit sells
the held position to cash; the strategy never opens a short. Position
units estimated as size_usd / mid on entry; fill-delivery plumbing
corrects later.

Snapshot contract: {'mid_price': Decimal, 'ma_fast': Decimal,
'ma_slow': Decimal}. The features stack provides EMAs; the supporting
task maps them to ma_fast / ma_slow (e.g. EMA-55 as the ~MA50 proxy).

State: in_position, position_units, entry_count, exit_count.

Spec §6.2 compat: [TREND_UP] — the golden cross helps in sustained
uptrends; it whipsaws in chop and never longs a downtrend.
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


_COID_PREFIX = "trndma"


def _read_params(params: dict[str, Any]) -> Decimal:
    try:
        position_usd = Decimal(str(params["position_usd"]))
    except KeyError as e:
        raise KeyError(
            f"trend_ma_crossover params missing required key: {e}"
        ) from e
    if position_usd <= Decimal("0"):
        raise ValueError(f"position_usd must be > 0, got {position_usd}")
    return position_usd


class MaCrossoverStrategy(Strategy):
    """D1 MA Crossover — long while fast MA > slow MA."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "ma_fast", "ma_slow"):
            if k not in snapshot:
                raise KeyError(f"trend_ma_crossover requires snapshot[{k!r}]")
        mid = Decimal(str(snapshot["mid_price"]))
        ma_fast = Decimal(str(snapshot["ma_fast"]))
        ma_slow = Decimal(str(snapshot["ma_slow"]))
        position_usd = _read_params(ctx.params)

        bullish = ma_fast > ma_slow
        in_position = bool(ctx.state.get("in_position", False))
        units = Decimal(ctx.state.get("position_units", "0"))
        entry_count = int(ctx.state.get("entry_count", 0))
        exit_count = int(ctx.state.get("exit_count", 0))

        intents: list[OrderIntent] = []
        if bullish and not in_position:
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=position_usd,
                limit_price=mid,
                client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-entry-{entry_count}",
                role="entry",
            ))
            units = position_usd / mid
            in_position = True
            entry_count += 1
        elif not bullish and in_position:
            exit_value = units * mid
            if exit_value > Decimal("0"):
                intents.append(OrderIntent(
                    symbol=ctx.symbol,
                    order_type="limit",
                    size_usd=exit_value,
                    limit_price=mid,
                    client_order_id=f"{_COID_PREFIX}-{ctx.strategy_id}-exit-{exit_count}",
                    role="exit",
                ))
                exit_count += 1
            units = Decimal("0")
            in_position = False

        ctx.state["in_position"] = in_position
        ctx.state["position_units"] = str(units)
        ctx.state["entry_count"] = entry_count
        ctx.state["exit_count"] = exit_count
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: [TREND_UP].
        if regime == Regime.TREND_UP:
            return ReturnExpectation(
                monthly_return_pct=Decimal("0.03"),
                confidence=0.5,
                rationale="Golden cross rides sustained uptrends",
            )
        return ReturnExpectation(
            monthly_return_pct=Decimal("0"),
            confidence=0.6,
            rationale="Whipsaws in chop; no long in downtrend",
        )
