"""C4 HODL++ (Grid + Rebalance) — Phase 3 Wave 1 Task 2.17.

A composite of two legs sharing the strategy's allocated capital:

  core leg : core_fraction * capital, periodically rebalanced to that
             target value (like C1 Periodic Rebalancing)
  grid leg : (1 - core_fraction) * capital, run as a standard
             evenly-spaced buy ladder between grid_low/grid_high with
             sell-against-fill (like A1 Standard Grid)

Both legs may emit intents in the same tick. State is nested:

  {"core": {position_units, rebalance_count, last_rebalance_at},
   "grid": {"levels": [ {price, side, submitted, filled_buy,
                         submitted_sell, client_order_id}, ... ]}}

First tick: deploys the grid ladder AND rebalances the core (from a
flat start that's a buy to the core target). Subsequent ticks: the
core rebalances only after core_interval_seconds; the grid emits a
sell at rung i+1 whenever rung i's filled_buy is True (worker-
delivered, same as A1).

Halal-spot inviolable: side='long' on every intent. The grid leg's
sells reduce filled buys; the core leg's sell (if the core
appreciates above target) caps at the held core value — never short.
Position units are estimated as size_usd / mid on a buy; fill-
delivery plumbing corrects later.

The grid leg's deploy + sell logic is written inline here rather than
calling grid/_base.py's helpers because HODL++ needs a distinct
client_order_id namespace ('hodlpp-{sid}-grid-L{i}-...') that the
helpers' fixed format doesn't produce. It's a small amount of code
and keeping the composite self-explanatory beats contorting the
shared helper.

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP] — the grid
leg pays in chop; the core leg carries the uptrend.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)
from trading_sandwich.strategies.rebalance._base import rebalance_toward_value


_COID_PREFIX = "hodlpp"


def _read_params(
    params: dict[str, Any],
) -> tuple[Decimal, int, Decimal, Decimal, int]:
    try:
        core_fraction = Decimal(str(params["core_fraction"]))
        core_interval = int(params["core_interval_seconds"])
        grid_low = Decimal(str(params["grid_low"]))
        grid_high = Decimal(str(params["grid_high"]))
        grid_levels = int(params["grid_levels"])
    except KeyError as e:
        raise KeyError(
            f"hodl_plus_plus params missing required key: {e}"
        ) from e
    if core_fraction <= Decimal("0") or core_fraction >= Decimal("1"):
        raise ValueError(
            f"core_fraction must be in (0, 1) — leaves room for the grid; "
            f"got {core_fraction}"
        )
    if core_interval <= 0:
        raise ValueError(f"core_interval_seconds must be > 0, got {core_interval}")
    if grid_low >= grid_high:
        raise ValueError(f"grid_low ({grid_low}) must be < grid_high ({grid_high})")
    if grid_levels < 2:
        raise ValueError(f"grid_levels must be >= 2, got {grid_levels}")
    return core_fraction, core_interval, grid_low, grid_high, grid_levels


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


def _evenly_spaced(low: Decimal, high: Decimal, n: int) -> list[Decimal]:
    step = (high - low) / Decimal(n - 1)
    return [low + step * Decimal(i) for i in range(n)]


class HodlPlusPlusStrategy(Strategy):
    """C4 HODL++ — a rebalanced core with a grid layered on top."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "mid_price"):
            if k not in snapshot:
                raise KeyError(f"hodl_plus_plus requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        mid = Decimal(str(snapshot["mid_price"]))
        (core_fraction, core_interval, grid_low, grid_high,
         grid_levels) = _read_params(ctx.params)

        capital = ctx.capital_allocated_usd
        core_budget = core_fraction * capital
        grid_budget = (Decimal("1") - core_fraction) * capital
        grid_size_per_level = grid_budget / Decimal(grid_levels)

        intents: list[OrderIntent] = []
        intents.extend(self._grid_leg(ctx, mid, grid_low, grid_high,
                                      grid_levels, grid_size_per_level))
        intents.extend(self._core_leg(ctx, now, mid, core_budget,
                                      core_interval))
        return intents

    # --- grid leg --------------------------------------------------------

    def _grid_leg(
        self,
        ctx: StrategyContext,
        mid: Decimal,
        low: Decimal,
        high: Decimal,
        n_levels: int,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        grid_state = ctx.state.setdefault("grid", {})
        if not grid_state.get("levels"):
            return self._deploy_grid(ctx, grid_state, mid, low, high,
                                     n_levels, size_per_level)
        return self._grid_sells_for_fills(ctx, grid_state, size_per_level)

    def _deploy_grid(
        self,
        ctx: StrategyContext,
        grid_state: dict,
        mid: Decimal,
        low: Decimal,
        high: Decimal,
        n_levels: int,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        prices = _evenly_spaced(low, high, n_levels)
        intents: list[OrderIntent] = []
        levels_state: list[dict[str, Any]] = []
        for i, price in enumerate(prices):
            should_submit = price <= mid
            coid = f"{_COID_PREFIX}-{ctx.strategy_id}-grid-L{i}-entry"
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
        grid_state["levels"] = levels_state
        return intents

    def _grid_sells_for_fills(
        self,
        ctx: StrategyContext,
        grid_state: dict,
        size_per_level: Decimal,
    ) -> list[OrderIntent]:
        levels = grid_state["levels"]
        intents: list[OrderIntent] = []
        for i, lvl in enumerate(levels):
            if not lvl.get("filled_buy"):
                continue
            if lvl.get("submitted_sell"):
                continue
            if i + 1 >= len(levels):
                continue
            sell_price = Decimal(levels[i + 1]["price"])
            sell_coid = f"{_COID_PREFIX}-{ctx.strategy_id}-grid-L{i + 1}-exit"
            intents.append(OrderIntent(
                symbol=ctx.symbol,
                order_type="limit",
                size_usd=size_per_level,
                limit_price=sell_price,
                client_order_id=sell_coid,
                role="exit",
                direction="sell",
                grid_level=i + 1,
            ))
            lvl["submitted_sell"] = True
        return intents

    # --- core leg --------------------------------------------------------

    def _core_leg(
        self,
        ctx: StrategyContext,
        now: datetime,
        mid: Decimal,
        core_budget: Decimal,
        core_interval: int,
    ) -> list[OrderIntent]:
        core_state = ctx.state.setdefault("core", {})
        last_iso = core_state.get("last_rebalance_at")
        if last_iso is not None:
            last_at = datetime.fromisoformat(last_iso)
            if (now - last_at).total_seconds() < core_interval:
                return []

        count = int(core_state.get("rebalance_count", 0))
        units = Decimal(core_state.get("position_units", "0"))

        intents, new_units = rebalance_toward_value(
            ctx=ctx, target_value=core_budget, mid=mid, units=units,
            coid_prefix=f"{_COID_PREFIX}-core",
            # rebalance_toward_value builds "{prefix}-{sid}-rb{seq}", so
            # this yields "hodlpp-core-{sid}-rb{n}" — distinct from the
            # grid leg's "hodlpp-{sid}-grid-..." namespace.
            seq=count,
        )
        core_state["rebalance_count"] = count + 1
        core_state["position_units"] = str(new_units)
        core_state["last_rebalance_at"] = now.isoformat()
        return intents

    # --- lifecycle -------------------------------------------------------

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP].
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.03"),
                    confidence=0.5,
                    rationale="Grid leg harvests chop; core just sits",
                )
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.02"),
                    confidence=0.45,
                    rationale="Core carries the trend; grid contributes a bit",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.4,
                    rationale="Modest grid harvest, flat core",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.6,
                    rationale="Downtrend: grid bleeds, core draws down",
                )
