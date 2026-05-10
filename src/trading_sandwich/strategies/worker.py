"""Strategy worker tick loop — Phase 3 plan Task 1.15.

Single Celery beat task `strategies_tick_celery` that fires every 30s
and ticks every active strategy in the DB. The pure-async core
`tick_all_strategies(registry)` is the unit testable surface; the
Celery wrapper is one line.

The registry maps strategy_type strings to Strategy classes. It's
passed as a parameter so the same worker code runs in tests (with
test stubs) and in production (with real Wave-1+ strategies). The
default production registry is in `_default_registry()` and grows
as Wave 1 strategies land.

Tick contract per strategy (in tick_all_strategies):

  load StrategyRow from list_active() → filter to active-only →
  fetch state (or None if first tick) → build StrategyContext →
  call cls().tick(ctx, snapshot={}) → save state with optimistic lock →
  update last_tick_at.

On any exception inside tick(): catch → mark_errored with the
exception message → log → continue with next strategy. One bad
strategy never takes down the worker.

Phase 0 of the worker emits NO orders to the execution rail. The
returned OrderIntent list is logged for observability and stored as
strategy_orders (Wave 1 task). Submitting to ccxt is wired in Wave 1
when real strategies need it.

Snapshot is empty `{}` for now — Wave 1 work plumbs in the latest
features row + recent prices. Strategies that need data are Wave 1+;
Wave 0 only ships NoOpStrategy in the smoke test.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.strategies import repo
from trading_sandwich.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyStatus,
)


logger = logging.getLogger(__name__)


@dataclass
class TickReport:
    ticked: int = 0           # strategies whose tick() ran successfully
    skipped: int = 0          # unknown strategy_type (no class in registry)
    skipped_paused: int = 0   # paused strategies (in list_active but not ticked)
    errored: int = 0          # strategies whose tick() raised → marked errored


def _default_registry() -> dict[str, type[Strategy]]:
    """Production registry. Wave 1 strategies register here as they land.
    Empty for Wave 0 — the foundation smoke test injects its own
    NoOpStrategy."""
    from trading_sandwich.strategies.grid.infinity import InfinityGridStrategy
    from trading_sandwich.strategies.grid.standard import StandardGridStrategy

    return {
        "grid_standard": StandardGridStrategy,
        "grid_infinity": InfinityGridStrategy,
    }


async def _tick_one_strategy(
    row: repo.StrategyRow,
    cls: type[Strategy],
) -> bool:
    """Tick one strategy. Returns True on success, raises on failure
    (caller catches + marks_errored + counts)."""
    instance = cls()

    state_row = await repo.get_state(row.id)
    state = dict(state_row.state) if state_row is not None else {}
    expected_updated_at = state_row.updated_at if state_row is not None else None

    ctx = StrategyContext(
        strategy_id=row.id,
        strategy_type=row.strategy_type,
        symbol=row.symbol,
        params=dict(row.params),
        state=state,
        capital_allocated_usd=row.capital_allocated_usd,
        capital_deployed_usd=row.capital_deployed_usd,
    )

    intents = instance.tick(ctx, snapshot={})

    # Persist state if the strategy mutated ctx.state. We always call
    # save_state — first tick or re-tick — so the upsert is idempotent.
    await repo.save_state(
        row.id, ctx.state, expected_updated_at=expected_updated_at,
    )
    await repo.update_last_tick_at(row.id)

    if intents:
        logger.info(
            "strategy %d (%s on %s) emitted %d intents",
            row.id, row.strategy_type, row.symbol, len(intents),
        )
    return True


async def tick_all_strategies(
    *,
    registry: dict[str, type[Strategy]] | None = None,
) -> TickReport:
    """Tick every active strategy. paused strategies are listed but not
    ticked. Returns a TickReport summarizing the cycle."""
    reg = registry if registry is not None else _default_registry()
    report = TickReport()

    rows = await repo.list_active()
    for row in rows:
        if row.status == StrategyStatus.PAUSED:
            report.skipped_paused += 1
            continue
        cls = reg.get(row.strategy_type)
        if cls is None:
            logger.error(
                "strategy %d (%s on %s): unknown strategy_type, skipping",
                row.id, row.strategy_type, row.symbol,
            )
            report.skipped += 1
            continue
        try:
            await _tick_one_strategy(row, cls)
            report.ticked += 1
        except Exception as exc:
            logger.exception(
                "strategy %d (%s on %s): tick raised, marking errored",
                row.id, row.strategy_type, row.symbol,
            )
            try:
                await repo.mark_errored(
                    row.id, error_message=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception(
                    "strategy %d: failed to mark errored after tick crash",
                    row.id,
                )
            report.errored += 1

    return report


# --- Celery wrappers --------------------------------------------------------


@app.task(name="trading_sandwich.strategies.worker.strategies_tick_celery")
def strategies_tick_celery() -> dict:
    """Celery beat entrypoint. Fires every 30s (configured in
    celery_app beat_schedule). Returns the report dict for
    Celery's own logging."""
    report = run_coro(tick_all_strategies())
    return {
        "ticked": report.ticked,
        "skipped": report.skipped,
        "skipped_paused": report.skipped_paused,
        "errored": report.errored,
    }
