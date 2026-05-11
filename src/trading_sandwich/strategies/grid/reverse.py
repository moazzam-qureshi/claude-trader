"""A4 Reverse Grid — Phase 3 Wave 1 Task 2.4.

Inverse of A1 Standard Grid. Assumes the operator already holds
inventory and wants to harvest rallies + rebuy dips:

  First tick: sell LIMIT at every rung at-or-ABOVE mid_price.
  When rung i's sell fills (state['levels'][i]['filled_sell']=True,
  delivered by the worker/execution rail), the next tick emits a buy
  at rung i-1 — rebuy the dip.

Halal-spot inviolable: every emitted intent has side='long'. The
'reversal' here is direction-of-action (sell first, then rebuy), not
side. Sells are role='exit' (exiting existing inventory); rebuys are
role='entry'.

State per rung: {price, side: 'sell', submitted, filled_sell,
submitted_rebuy, client_order_id}. The `side` field marks the rung's
INITIAL action — distinct from the OrderIntent.side field (which is
always 'long').

Capital sizing: each rung sells/rebuys capital_allocated_usd / levels.
Capital here represents the inventory the strategy is allocated to
manage.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]. Best when
already holding the asset.

Self-contained: the buy-first helpers in _base.py don't apply because
the rung schema and direction-of-action are different. If A4-style
strategies multiply later, abstract then.
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


_COID_PREFIX = "gridrev"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, int]:
    try:
        low = Decimal(str(params["low"]))
        high = Decimal(str(params["high"]))
        levels = int(params["levels"])
    except KeyError as e:
        raise KeyError(f"grid_reverse params missing required key: {e}") from e
    if levels < 2:
        raise ValueError(f"levels must be >= 2, got {levels}")
    if low >= high:
        raise ValueError(f"low ({low}) must be < high ({high})")
    return low, high, levels


def _evenly_spaced_levels(low: Decimal, high: Decimal, n: int) -> list[Decimal]:
    step = (high - low) / Decimal(n - 1)
    return [low + step * Decimal(i) for i in range(n)]


class ReverseGridStrategy(Strategy):
    """A4 Reverse Grid — sell-first ladder with rebuy on dips."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("grid_reverse requires snapshot['mid_price']")
        mid = Decimal(str(snapshot["mid_price"]))
        low, high, n_levels = _read_params(ctx.params)
        size_per_level = ctx.capital_allocated_usd / Decimal(n_levels)

        if not ctx.state.get("levels"):
            return self._deploy_sell_ladder(
                ctx, low, high, n_levels, mid, size_per_level,
            )

        return self._emit_rebuys_for_fills(ctx, size_per_level)

    def _deploy_sell_ladder(
        self,
        ctx: StrategyContext,
        low: Decimal,
        high: Decimal,
        n_levels: int,
        mid: Decimal,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        prices = _evenly_spaced_levels(low, high, n_levels)
        intents: list[OrderIntent] = []
        levels_state: list[dict[str, Any]] = []
        for i, price in enumerate(prices):
            should_submit = price >= mid
            coid = f"{_COID_PREFIX}-{ctx.strategy_id}-L{i}-exit"
            if should_submit:
                intents.append(OrderIntent(
                    symbol=ctx.symbol,
                    order_type="limit",
                    size_usd=size_per_level,
                    limit_price=price,
                    client_order_id=coid,
                    role="exit",
                    direction="sell",
                    grid_level=i,
                ))
            levels_state.append({
                "price": str(price),
                "side": "sell",
                "submitted": should_submit,
                "filled_sell": False,
                "submitted_rebuy": False,
                "client_order_id": coid,
            })
        ctx.state["levels"] = levels_state
        return intents

    def _emit_rebuys_for_fills(
        self,
        ctx: StrategyContext,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        levels = ctx.state["levels"]
        intents: list[OrderIntent] = []
        for i, lvl in enumerate(levels):
            if not lvl.get("filled_sell"):
                continue
            if lvl.get("submitted_rebuy"):
                continue
            # No rung below the bottom — strategy has hit its floor.
            if i - 1 < 0:
                continue
            rebuy_price = Decimal(levels[i - 1]["price"])
            rebuy_coid = f"{_COID_PREFIX}-{ctx.strategy_id}-L{i - 1}-entry"
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=size_per_level,
                limit_price=rebuy_price,
                client_order_id=rebuy_coid,
                role="entry",
                grid_level=i - 1,
            ))
            lvl["submitted_rebuy"] = True
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP].
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.03"),
                    confidence=0.55,
                    rationale="Range capture from inventory + rebuy chop",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.025"),
                    confidence=0.5,
                    rationale="Sells into strength; rebuys lag uptrend",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.45,
                    rationale="Less chop, smaller harvest",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: prefer to stand down",
                )
