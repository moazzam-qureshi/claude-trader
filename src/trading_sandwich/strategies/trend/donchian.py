"""D2 Donchian Breakout (Turtle) — Phase 3 Wave 1 Task 2.19.

Binary in/out trend follower, Turtle-style: long on a breakout above
the N-bar high, exit on a break below the M-bar low. The asymmetric
channel — entry on the high, exit on the low — is the built-in
whipsaw filter; the band between the two stops the position flapping.

  mid >= donchian_high and not in position → buy position_usd (entry)
  mid <= donchian_low  and     in position → sell the whole position (exit)
  otherwise → no-op (hold, or stay flat between the bands)

Halal-spot inviolable: side='long' on every intent. The exit sells
the held position to cash; the strategy sits out the downside instead
of shorting it. Position units estimated as size_usd / mid on entry;
fill-delivery plumbing corrects later.

Snapshot contract: {'mid_price': Decimal, 'donchian_high': Decimal,
'donchian_low': Decimal} — donchian_high is the highest high over the
entry lookback (e.g. 20 bars), donchian_low the lowest low over the
exit lookback (e.g. 10 bars). The supporting task computes them from
klines.

State: in_position, position_units, entry_count, exit_count — same
shape as D1 MA Crossover. Once a third trend strategy lands the shared
binary in/out plumbing gets lifted into trend/_base.py.

Spec §6.2 compat: [TREND_UP, TREND_DOWN] — breakouts pay in strong
directional moves; the long-only build longs the uptrend and sits out
the downtrend (avoiding chop losses).
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


_COID_PREFIX = "trndon"


def _read_params(params: dict[str, Any]) -> Decimal:
    try:
        position_usd = Decimal(str(params["position_usd"]))
    except KeyError as e:
        raise KeyError(
            f"trend_donchian params missing required key: {e}"
        ) from e
    if position_usd <= Decimal("0"):
        raise ValueError(f"position_usd must be > 0, got {position_usd}")
    return position_usd


class DonchianBreakoutStrategy(Strategy):
    """D2 Donchian Breakout — long the N-bar high, exit the M-bar low."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "donchian_high", "donchian_low"):
            if k not in snapshot:
                raise KeyError(f"trend_donchian requires snapshot[{k!r}]")
        mid = Decimal(str(snapshot["mid_price"]))
        high = Decimal(str(snapshot["donchian_high"]))
        low = Decimal(str(snapshot["donchian_low"]))
        if low > high:
            raise ValueError(
                f"donchian_low ({low}) must be <= donchian_high ({high})"
            )
        position_usd = _read_params(ctx.params)

        return apply_binary_trend_signal(
            ctx=ctx,
            enter_signal=mid >= high,
            exit_signal=mid <= low,
            position_usd=position_usd,
            mid=mid,
            coid_prefix=_COID_PREFIX,
        )

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: [TREND_UP, TREND_DOWN].
        match regime:
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.035"),
                    confidence=0.5,
                    rationale="Rides 20-bar-high breakouts in uptrends",
                )
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.005"),
                    confidence=0.4,
                    rationale="Sits flat below the channel — avoids the bleed",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.6,
                    rationale="Range chop whipsaws the channel",
                )
