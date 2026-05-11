"""Strategy fill-back loop — Blocker B (path to production).

When an order linked to a strategy fills, the strategy's
`strategy_state` must reflect it. Grid strategies decide whether to
place a paired sell (or rebuy) by reading per-rung flags in their
state — `levels[i]['filled_buy']` for the buy-ladder family (standard /
geometric / infinity / hodl++'s embedded grid), `levels[i]['filled_sell']`
for the reverse grid. Nothing flipped those flags until now, so grids
never placed their sell legs. This loop closes that.

Scope of this first cut: grid-rung flags only. The DCA / rebalance /
trend / mean-reversion families estimate position units as
size_usd / price at emit time; correcting that from the real
`OrderReceipt.filled_base` is a refinement those families can get
later — their orders carry no `grid_level`, so this loop skips them.

Runs as a Celery beat task, the same shape as paper_match. Idempotent:
a rung already marked filled is not re-written, so the loop doesn't
churn the optimistic-lock version when there's nothing to do.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.config import get_settings
from trading_sandwich.strategies import repo
from trading_sandwich.strategies.repo import StaleStateError


logger = logging.getLogger(__name__)


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def _find_levels(state: dict) -> list | None:
    """Locate the per-rung list in a strategy's state. Most grids keep
    it at state['levels']; hodl++ nests it under state['grid']['levels'].
    Returns the list (mutable, part of `state`) or None if absent."""
    lvls = state.get("levels")
    if isinstance(lvls, list):
        return lvls
    grid = state.get("grid")
    if isinstance(grid, dict) and isinstance(grid.get("levels"), list):
        return grid["levels"]
    return None


def _flag_for(role: str, rung: dict) -> str | None:
    """Which rung flag a fill of this role should set. A buy-ladder rung
    has 'filled_buy'; a reverse-grid rung has 'filled_sell'. We key off
    whichever flag the rung dict actually carries so the loop doesn't
    need to know the strategy type."""
    if role == "entry" and "filled_buy" in rung:
        return "filled_buy"
    if role == "exit" and "filled_sell" in rung:
        return "filled_sell"
    return None


async def _filled_grid_links() -> list[tuple[int, str, int]]:
    """(strategy_id, role, grid_level) for every strategy_orders row
    whose order is filled and that carries a grid_level."""
    engine = _engine()
    try:
        async with engine.connect() as conn:
            r = await conn.execute(text(
                "SELECT so.strategy_id, so.role, so.grid_level "
                "FROM strategy_orders so JOIN orders o ON o.order_id = so.order_id "
                "WHERE o.status = 'filled' AND so.grid_level IS NOT NULL"
            ))
            return [(int(row[0]), row[1], int(row[2])) for row in r]
    finally:
        await engine.dispose()


async def apply_strategy_fills() -> int:
    """Walk filled grid-linked orders and flip the matching rung flag in
    each strategy's state. Returns the number of state mutations applied
    (0 if every fill was already reflected)."""
    links = await _filled_grid_links()
    if not links:
        return 0

    # Group by strategy so we apply all of a strategy's pending fills in
    # one read-modify-write (one optimistic-lock cycle per strategy).
    by_strategy: dict[int, list[tuple[str, int]]] = {}
    for sid, role, gl in links:
        by_strategy.setdefault(sid, []).append((role, gl))

    applied = 0
    for sid, fills in by_strategy.items():
        state_row = await repo.get_state(sid)
        if state_row is None:
            continue
        state = dict(state_row.state)
        levels = _find_levels(state)
        if levels is None:
            continue

        changed = False
        for role, gl in fills:
            if gl < 0 or gl >= len(levels):
                logger.warning(
                    "strategy %d: fill grid_level %d out of range (%d rungs)",
                    sid, gl, len(levels),
                )
                continue
            rung = levels[gl]
            flag = _flag_for(role, rung)
            if flag is None:
                continue
            if not rung.get(flag):
                rung[flag] = True
                changed = True

        if not changed:
            continue
        try:
            await repo.save_state(
                sid, state, expected_updated_at=state_row.updated_at,
            )
            applied += 1
        except StaleStateError:
            # The strategy ticked between our read and write; its tick
            # will re-derive from the same DB rows next pass.
            logger.info("strategy %d: stale state on fill-apply, retry next pass", sid)
    return applied


@app.task(name="trading_sandwich.strategies.fill_apply.apply_fills")
def apply_fills() -> int:
    return run_coro(apply_strategy_fills())
