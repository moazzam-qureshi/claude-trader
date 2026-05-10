"""A2 Infinity Grid — Phase 3 Wave 1 Task 2.2.

Range/volatility capture with an open top: same buy-ladder + sell-against-
fill mechanic as A1 Standard Grid, but with two differences:

  1. Geometric rung spacing instead of arithmetic. Rung i sits at
     low * (1 + step_pct) ** i. Better for low-priced alts where a
     fixed dollar spacing produces uneven percent moves.
  2. The grid expands upward as price climbs. When mid_price reaches
     within one step of the current top rung, the next tick spawns a
     new top rung at top * (1 + step_pct). This is the "infinity" —
     no fixed cap, captures uptrend drift.

Halal-spot only: every intent has side='long', role='entry'|'exit'.

State shape (persisted in strategy_state.state JSONB):

    {
      "step_pct": "0.02",
      "levels": [
        {"price": "100", "side": "buy", "submitted": True,
         "filled_buy": False, "submitted_sell": False,
         "client_order_id": "gridinf-202-L0-entry"},
        ...
      ]
    }

Fill *delivery* (flipping filled_buy=True) is the worker/execution
rail's responsibility. The strategy only reads.

A1 Standard Grid and A2 Infinity Grid have meaningful overlap in
fill-handling logic; once A3 (Geometric Grid) lands and we have three
grid variants, common code can be lifted into a shared helper. Until
then, duplication is cheaper than premature abstraction.
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


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, int]:
    try:
        low = Decimal(str(params["low"]))
        step_pct = Decimal(str(params["step_pct"]))
        levels = int(params["levels"])
    except KeyError as e:
        raise KeyError(f"grid_infinity params missing required key: {e}") from e
    if levels < 2:
        raise ValueError(f"levels must be >= 2, got {levels}")
    if step_pct <= Decimal("0"):
        raise ValueError(f"step_pct must be > 0, got {step_pct}")
    if low <= Decimal("0"):
        raise ValueError(f"low must be > 0, got {low}")
    return low, step_pct, levels


def _geometric_levels(low: Decimal, step_pct: Decimal, n: int) -> list[Decimal]:
    one_plus_step = Decimal("1") + step_pct
    return [low * (one_plus_step ** i) for i in range(n)]


class InfinityGridStrategy(Strategy):
    """A2 Infinity Grid — geometric ladder that expands upward."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("grid_infinity requires snapshot['mid_price']")
        mid = Decimal(str(snapshot["mid_price"]))
        low, step_pct, n_levels = _read_params(ctx.params)
        size_per_level = ctx.capital_allocated_usd / Decimal(n_levels)

        if not ctx.state.get("levels"):
            return self._deploy_ladder(ctx, low, step_pct, n_levels, mid, size_per_level)

        intents = self._emit_sells_for_fills(ctx, size_per_level)
        self._maybe_expand_upward(ctx, mid, step_pct)
        return intents

    def _deploy_ladder(
        self,
        ctx: StrategyContext,
        low: Decimal,
        step_pct: Decimal,
        n_levels: int,
        mid: Decimal,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        prices = _geometric_levels(low, step_pct, n_levels)
        intents: list[OrderIntent] = []
        levels_state: list[dict[str, Any]] = []
        for i, price in enumerate(prices):
            should_submit = price <= mid
            coid = f"gridinf-{ctx.strategy_id}-L{i}-entry"
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
        ctx.state["step_pct"] = str(step_pct)
        ctx.state["levels"] = levels_state
        return intents

    def _emit_sells_for_fills(
        self,
        ctx: StrategyContext,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
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
            sell_coid = f"gridinf-{ctx.strategy_id}-L{i + 1}-exit"
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=size_per_level,
                limit_price=sell_price,
                client_order_id=sell_coid,
                role="exit",
                grid_level=i + 1,
            ))
            lvl["submitted_sell"] = True
        return intents

    def _maybe_expand_upward(
        self,
        ctx: StrategyContext,
        mid: Decimal,
        step_pct: Decimal,
    ) -> None:
        """If mid is within one step of the top rung, spawn a new top
        rung at top * (1 + step_pct). Sell-target only — submitted=False,
        filled_buy=False. At most one new rung per tick to keep the
        expansion bounded and predictable."""
        levels = ctx.state["levels"]
        top = Decimal(levels[-1]["price"])
        threshold = top * (Decimal("1") - step_pct)
        if mid < threshold:
            return
        new_top = top * (Decimal("1") + step_pct)
        new_index = len(levels)
        levels.append({
            "price": str(new_top),
            "side": "buy",
            "submitted": False,
            "filled_buy": False,
            "submitted_sell": False,
            "client_order_id": f"gridinf-{ctx.strategy_id}-L{new_index}-entry",
        })

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # spec §2.1: best in RANGE_VOLATILE + slight TREND_UP.
        # Compat (§6.2): [RANGE_VOLATILE, TREND_UP].
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.035"),
                    confidence=0.6,
                    rationale="Range capture + uptrend drift bonus",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.025"),
                    confidence=0.5,
                    rationale="Sells into strength as grid expands upward",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: prefer to stand down",
                )
