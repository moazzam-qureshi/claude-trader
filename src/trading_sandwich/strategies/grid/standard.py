"""A1 Standard Grid — Phase 3 Wave 1 Task 2.1.

Range/volatility capture: a fixed price ladder between `low` and `high`
with `levels` evenly-spaced rungs. On first tick the strategy emits a
buy LIMIT at every rung whose price is at or below the snapshot
mid-price. Sells will be placed against fills in a follow-up Wave 1
task once execution-side fill plumbing lands; this first cut emits the
initial buy ladder only.

Halal-spot only: every intent has side='long', role='entry'. The
strategy never proposes shorts, perps, or leverage — those constraints
are enforced both here and in OrderIntent's Pydantic schema.

State shape (persisted in strategy_state.state JSONB):

    {
      "levels": [
        {"price": "60000", "side": "buy", "submitted": True,
         "client_order_id": "gridstd-101-L0-entry"},
        ...
      ]
    }

Idempotency: subsequent ticks see `levels` already populated and emit
no new intents. Re-tick must NOT mutate state — same dict in, same
dict out — so the optimistic-lock save is a no-op.
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


def _evenly_spaced_levels(low: Decimal, high: Decimal, n: int) -> list[Decimal]:
    """Return n prices, inclusive of both endpoints, evenly spaced."""
    if n < 2:
        raise ValueError(f"levels must be >= 2, got {n}")
    if low >= high:
        raise ValueError(f"low ({low}) must be < high ({high})")
    step = (high - low) / Decimal(n - 1)
    return [low + step * Decimal(i) for i in range(n)]


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, int]:
    """Pull (low, high, levels) out of the params dict, coercing to the
    right types. Raises KeyError on missing, ValueError on invalid."""
    try:
        low = Decimal(str(params["low"]))
        high = Decimal(str(params["high"]))
        levels = int(params["levels"])
    except KeyError as e:
        raise KeyError(f"grid_standard params missing required key: {e}") from e
    return low, high, levels


class StandardGridStrategy(Strategy):
    """A1 Standard Grid — fixed-range buy ladder."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        if "mid_price" not in snapshot:
            raise KeyError("grid_standard requires snapshot['mid_price']")
        mid = Decimal(str(snapshot["mid_price"]))
        low, high, n_levels = _read_params(ctx.params)
        size_per_level = ctx.capital_allocated_usd / Decimal(n_levels)

        # First tick: deploy the buy ladder.
        if not ctx.state.get("levels"):
            return self._deploy_ladder(ctx, low, high, n_levels, mid, size_per_level)

        # Subsequent ticks: any rung whose buy has filled but whose
        # paired sell hasn't been submitted yet → emit that sell at the
        # next-higher rung's price. Fill *delivery* (setting filled_buy)
        # is the worker/execution rail's job; this strategy only reads.
        return self._emit_sells_for_fills(ctx, size_per_level)

    def _deploy_ladder(
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
            should_submit = price <= mid
            coid = f"gridstd-{ctx.strategy_id}-L{i}-entry"
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
            # No rung above the top rung — nothing to sell into.
            if i + 1 >= len(levels):
                continue
            sell_price = Decimal(levels[i + 1]["price"])
            sell_coid = f"gridstd-{ctx.strategy_id}-L{i + 1}-exit"
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
        # Open-order cancellation is the worker's responsibility (it
        # holds the exchange-side order IDs); the strategy emits no
        # new intents during wind-down.
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        # Same reasoning as graceful_shutdown. A market-flatten of
        # accumulated inventory is the kill-switch's job, wired
        # through the execution rail — not this strategy.
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # spec §2.1: A1 best in RANGE_VOLATILE / RANGE_QUIET; spec §6.2
        # compatibility map also lists TREND_UP. Numbers are first-cut
        # estimates; the performance tracker (Task 1.10) compares actual
        # vs expected and Claude tunes from there.
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.03"),
                    confidence=0.6,
                    rationale="Range capture: chop pays the grid",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.015"),
                    confidence=0.5,
                    rationale="Less chop, smaller harvest",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.01"),
                    confidence=0.4,
                    rationale="Some grid pays even in mild uptrend",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: prefer to stand down",
                )
