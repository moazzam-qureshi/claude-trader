"""A3 Geometric Grid — Phase 3 Wave 1 Task 2.3.

Percentage-spaced ladder with a fixed top: between A1 Standard Grid
(arithmetic spacing, fixed top) and A2 Infinity Grid (geometric
spacing, expanding top). Better than A1 for low-priced alts where
fixed-dollar rung spacing would produce wildly uneven percent moves.

Mechanic: rung i sits at low * (1 + pct_spacing) ** i. levels rungs
total. First tick deploys buy LIMITs at every rung at-or-below
mid_price. Subsequent ticks emit a sell at rung i+1 whenever rung i's
filled_buy is True (delivered by the worker/execution rail).

Halal-spot only: every intent has side='long', role='entry'|'exit'.

State shape:
    {
      "pct_spacing": "0.02",
      "levels": [
        {"price", "side", "submitted", "filled_buy", "submitted_sell",
         "client_order_id"},
        ...
      ]
    }

There is significant overlap with A1 and A2 in the deploy + sell-against-
fill logic. Lifting common helpers into strategies/grid/_base.py is the
next commit (refactor, no behavior change). Until then, keeping the three
self-contained makes individual review easier.
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
        pct_spacing = Decimal(str(params["pct_spacing"]))
        levels = int(params["levels"])
    except KeyError as e:
        raise KeyError(f"grid_geometric params missing required key: {e}") from e
    if levels < 2:
        raise ValueError(f"levels must be >= 2, got {levels}")
    if pct_spacing <= Decimal("0"):
        raise ValueError(f"pct_spacing must be > 0, got {pct_spacing}")
    if low <= Decimal("0"):
        raise ValueError(f"low must be > 0, got {low}")
    return low, pct_spacing, levels


def _geometric_levels(low: Decimal, pct_spacing: Decimal, n: int) -> list[Decimal]:
    one_plus = Decimal("1") + pct_spacing
    return [low * (one_plus ** i) for i in range(n)]


class GeometricGridStrategy(Strategy):
    """A3 Geometric Grid — fixed-top, percentage-spaced ladder."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("grid_geometric requires snapshot['mid_price']")
        mid = Decimal(str(snapshot["mid_price"]))
        low, pct_spacing, n_levels = _read_params(ctx.params)
        size_per_level = ctx.capital_allocated_usd / Decimal(n_levels)

        if not ctx.state.get("levels"):
            return self._deploy_ladder(
                ctx, low, pct_spacing, n_levels, mid, size_per_level,
            )

        return self._emit_sells_for_fills(ctx, size_per_level)

    def _deploy_ladder(
        self,
        ctx: StrategyContext,
        low: Decimal,
        pct_spacing: Decimal,
        n_levels: int,
        mid: Decimal,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        prices = _geometric_levels(low, pct_spacing, n_levels)
        intents: list[OrderIntent] = []
        levels_state: list[dict[str, Any]] = []
        for i, price in enumerate(prices):
            should_submit = price <= mid
            coid = f"gridgeo-{ctx.strategy_id}-L{i}-entry"
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
        ctx.state["pct_spacing"] = str(pct_spacing)
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
            sell_coid = f"gridgeo-{ctx.strategy_id}-L{i + 1}-exit"
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

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET].
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.03"),
                    confidence=0.55,
                    rationale="Geometric spacing handles alt vol better",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.015"),
                    confidence=0.5,
                    rationale="Less chop, smaller harvest",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: prefer to stand down",
                )
