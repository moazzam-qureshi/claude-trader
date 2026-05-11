"""D5 Multi-TF Alignment — Phase 3 Wave 1 Task 2.22.

Long only when three timeframes (1D, 4H, 1H) are all bullish; exit the
moment any one turns bearish. The triple-confirmation requirement is a
deliberately high bar — it sits out everything that isn't a strong,
broad uptrend.

  enter_signal = bullish_1d and bullish_4h and bullish_1h
  exit_signal  = not (all three bullish)

Halal-spot inviolable: side='long' on every intent. The exit sells
the held position to cash; never opens a short. Position units
estimated as size_usd / mid on entry; fill-delivery plumbing corrects
later. Plumbing shared with D1/D2/D3/D4 via trend/_base.py.

Snapshot contract: {'mid_price': Decimal, 'bullish_1d': bool,
'bullish_4h': bool, 'bullish_1h': bool}. The supporting task computes
each timeframe's bias (e.g. price above that timeframe's EMA).

State: in_position, position_units, entry_count, exit_count.

Spec §6.2 compat: [TREND_UP].
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


_COID_PREFIX = "trnmtf"


def _read_params(params: dict[str, Any]) -> Decimal:
    try:
        position_usd = Decimal(str(params["position_usd"]))
    except KeyError as e:
        raise KeyError(
            f"trend_multi_tf_alignment params missing required key: {e}"
        ) from e
    if position_usd <= Decimal("0"):
        raise ValueError(f"position_usd must be > 0, got {position_usd}")
    return position_usd


class MultiTfAlignmentStrategy(Strategy):
    """D5 Multi-TF Alignment — long only when 1D+4H+1H all bullish."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "bullish_1d", "bullish_4h", "bullish_1h"):
            if k not in snapshot:
                raise KeyError(
                    f"trend_multi_tf_alignment requires snapshot[{k!r}]"
                )
        mid = Decimal(str(snapshot["mid_price"]))
        position_usd = _read_params(ctx.params)

        all_bullish = bool(
            snapshot["bullish_1d"]
            and snapshot["bullish_4h"]
            and snapshot["bullish_1h"]
        )
        return apply_binary_trend_signal(
            ctx=ctx,
            enter_signal=all_bullish,
            exit_signal=not all_bullish,
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
                monthly_return_pct=Decimal("0.022"),
                confidence=0.5,
                rationale="Triple-TF confirmation = high-quality trend rides",
            )
        return ReturnExpectation(
            monthly_return_pct=Decimal("0"),
            confidence=0.65,
            rationale="High bar — sits out chop and downtrends entirely",
        )
