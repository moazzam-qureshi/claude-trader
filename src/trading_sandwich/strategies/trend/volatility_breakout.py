"""D3 Volatility Breakout — Phase 3 Wave 1 Task 2.20.

Binary in/out trend follower: go long when price breaks above a
reference level by more than k_atr * ATR — a volatility-scaled
breakout, so the trigger is bigger in turbulent markets and tighter
in quiet ones. Exit when price falls back to or below the reference.

  enter_signal = mid >= reference_price + k_atr * atr
  exit_signal  = mid <= reference_price
  (between → hold or stay flat)

Halal-spot inviolable: side='long' on every intent. The exit sells
the held position to cash; never opens a short. Position units
estimated as size_usd / mid on entry; fill-delivery plumbing corrects
later. Plumbing shared with D1/D2 via trend/_base.py.

Snapshot contract: {'mid_price': Decimal, 'reference_price': Decimal,
'atr': Decimal} — reference_price is the prior close / session open,
atr the ATR in price terms. The supporting task computes both (atr_14
from the feature stack; reference_price from the last completed bar).

State: in_position, position_units, entry_count, exit_count.

Spec §6.2 compat: [RANGE_QUIET, TREND_UP] — a quiet base that
suddenly expands is the textbook setup; once an uptrend is running
the breakouts keep working.
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


_COID_PREFIX = "trnvbo"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal]:
    try:
        position_usd = Decimal(str(params["position_usd"]))
        k_atr = Decimal(str(params["k_atr"]))
    except KeyError as e:
        raise KeyError(
            f"trend_volatility_breakout params missing required key: {e}"
        ) from e
    if position_usd <= Decimal("0"):
        raise ValueError(f"position_usd must be > 0, got {position_usd}")
    if k_atr <= Decimal("0"):
        raise ValueError(f"k_atr must be > 0, got {k_atr}")
    return position_usd, k_atr


class VolatilityBreakoutStrategy(Strategy):
    """D3 Volatility Breakout — long on a k_atr*ATR break above reference."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "reference_price", "atr"):
            if k not in snapshot:
                raise KeyError(
                    f"trend_volatility_breakout requires snapshot[{k!r}]"
                )
        mid = Decimal(str(snapshot["mid_price"]))
        reference = Decimal(str(snapshot["reference_price"]))
        atr = Decimal(str(snapshot["atr"]))
        if atr <= Decimal("0"):
            raise ValueError(f"atr must be > 0, got {atr}")
        position_usd, k_atr = _read_params(ctx.params)

        breakout_level = reference + k_atr * atr
        return apply_binary_trend_signal(
            ctx=ctx,
            enter_signal=mid >= breakout_level,
            exit_signal=mid <= reference,
            position_usd=position_usd,
            mid=mid,
            coid_prefix=_COID_PREFIX,
        )

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: [RANGE_QUIET, TREND_UP].
        match regime:
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.02"),
                    confidence=0.4,
                    rationale="Quiet base → tight trigger catches the expansion",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.025"),
                    confidence=0.45,
                    rationale="Breakouts keep firing in a running uptrend",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.6,
                    rationale="Volatile range whipsaws; no long in downtrend",
                )
