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
from trading_sandwich.strategies.trend._base import apply_binary_trend_signal


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
        return apply_binary_trend_signal(
            ctx=ctx,
            enter_signal=bullish,
            exit_signal=not bullish,
            position_usd=position_usd,
            mid=mid,
            coid_prefix=_COID_PREFIX,
        )

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
