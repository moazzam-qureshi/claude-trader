"""D4 Time-Series Momentum — Phase 3 Wave 1 Task 2.21.

The simplest possible trend filter: long while price is above the
N-day moving average, all cash while it's at or below.

  enter_signal = mid > ma_n
  exit_signal  = mid <= ma_n   (exactly one is always true)

Halal-spot inviolable: side='long' on every intent. The exit sells
the held position to cash; never opens a short. Position units
estimated as size_usd / mid on entry; fill-delivery plumbing corrects
later. Plumbing shared with D1/D2/D3 via trend/_base.py.

Snapshot contract: {'mid_price': Decimal, 'ma_n': Decimal} — ma_n is
the N-day moving average; the supporting task picks N and feeds the
value (e.g. EMA-200 for a long-horizon filter).

State: in_position, position_units, entry_count, exit_count.

Spec §6.2 compat: [TREND_UP] — above-MA is uptrend persistence; chop
whipsaws, downtrend means no long.
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


_COID_PREFIX = "trntsm"


def _read_params(params: dict[str, Any]) -> Decimal:
    try:
        position_usd = Decimal(str(params["position_usd"]))
    except KeyError as e:
        raise KeyError(
            f"trend_time_series_momentum params missing required key: {e}"
        ) from e
    if position_usd <= Decimal("0"):
        raise ValueError(f"position_usd must be > 0, got {position_usd}")
    return position_usd


class TimeSeriesMomentumStrategy(Strategy):
    """D4 Time-Series Momentum — long while above the N-day MA."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "ma_n"):
            if k not in snapshot:
                raise KeyError(
                    f"trend_time_series_momentum requires snapshot[{k!r}]"
                )
        mid = Decimal(str(snapshot["mid_price"]))
        ma_n = Decimal(str(snapshot["ma_n"]))
        position_usd = _read_params(ctx.params)

        above = mid > ma_n
        return apply_binary_trend_signal(
            ctx=ctx,
            enter_signal=above,
            exit_signal=not above,
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
                monthly_return_pct=Decimal("0.025"),
                confidence=0.45,
                rationale="Above-MA captures uptrend persistence",
            )
        return ReturnExpectation(
            monthly_return_pct=Decimal("0"),
            confidence=0.6,
            rationale="Whipsaws in chop; no long in downtrend",
        )
