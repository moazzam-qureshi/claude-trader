"""Strategy performance tracker — Phase 3 plan Task 1.10.

Computes per-strategy realized PnL over a window and compares to the
strategy's expected_return_for_regime to flag underperformers. Used by:

  - The Portfolio Strategist (read tool get_strategy_performance, Task
    1.11) when deciding whether to wind down or adjust allocations.
  - The strategy-worker auto-pause (Task 1.15+) when realized PnL falls
    below threshold for a sustained window.

Phase 0 of the tracker: realized PnL only. Unrealized (mark-to-market on
held positions) is deferred — needs current-price lookup that's not on
the path of this task.

Realized PnL math (per-strategy):

  entry_cost      = sum(filled_base * avg_fill_price + fees_usd) for
                    orders linked via strategy_orders with role='entry'
                    AND filled_at >= since
  exit_proceeds   = sum(filled_base * avg_fill_price - fees_usd) for
                    orders linked with role='exit' AND filled_at >= since
  realized_pnl    = exit_proceeds - entry_cost

This is exact when entries and exits balance over the window. If the
window cuts a round-trip in half, it understates PnL by the held base.
That's the right behavior for "what closed in this window."
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.strategies import repo as strategies_repo
from trading_sandwich.strategies.base import Regime, ReturnExpectation


@dataclass(frozen=True)
class PnLReport:
    strategy_id: int
    since: datetime | None
    entry_cost_usd: Decimal
    exit_proceeds_usd: Decimal
    realized_pnl_usd: Decimal
    entry_count: int
    exit_count: int


@dataclass(frozen=True)
class EvaluationReport:
    strategy_id: int
    current_regime: Regime
    window_days: int
    expected_pnl_usd: Decimal
    realized_pnl_usd: Decimal
    is_underperforming: bool
    threshold_pct: float


class _StrategyLike(Protocol):
    """Minimum surface needed by the tracker — just the
    expected_return_for_regime method. Lets us pass either a real
    Strategy subclass or a test stub."""

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation: ...


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def compute_realized_pnl(
    strategy_id: int,
    *,
    since: datetime | None = None,
) -> PnLReport:
    """Sum up filled entry and exit orders for `strategy_id` within
    [since, now]. Returns the realized-PnL breakdown."""
    engine = _engine()
    try:
        async with engine.connect() as conn:
            # Aggregated entry side
            entry_sql = (
                "SELECT COALESCE(SUM(o.filled_base * o.avg_fill_price + "
                "                    COALESCE(o.fees_usd, 0)), 0), "
                "       COUNT(*) "
                "FROM strategy_orders so "
                "JOIN orders o ON o.order_id = so.order_id "
                "WHERE so.strategy_id = :sid "
                "  AND so.role = 'entry' "
                "  AND o.status = 'filled' "
            )
            exit_sql = (
                "SELECT COALESCE(SUM(o.filled_base * o.avg_fill_price - "
                "                    COALESCE(o.fees_usd, 0)), 0), "
                "       COUNT(*) "
                "FROM strategy_orders so "
                "JOIN orders o ON o.order_id = so.order_id "
                "WHERE so.strategy_id = :sid "
                "  AND so.role = 'exit' "
                "  AND o.status = 'filled' "
            )
            params: dict = {"sid": strategy_id}
            if since is not None:
                entry_sql += "AND o.filled_at >= :since"
                exit_sql += "AND o.filled_at >= :since"
                params["since"] = since

            r = await conn.execute(text(entry_sql), params)
            entry_cost, entry_count = r.first()

            r = await conn.execute(text(exit_sql), params)
            exit_proceeds, exit_count = r.first()

            entry_cost = Decimal(entry_cost)
            exit_proceeds = Decimal(exit_proceeds)
            return PnLReport(
                strategy_id=strategy_id,
                since=since,
                entry_cost_usd=entry_cost,
                exit_proceeds_usd=exit_proceeds,
                realized_pnl_usd=exit_proceeds - entry_cost,
                entry_count=int(entry_count),
                exit_count=int(exit_count),
            )
    finally:
        await engine.dispose()


async def evaluate(
    *,
    strategy: _StrategyLike,
    strategy_id: int,
    current_regime: Regime,
    window_days: int = 30,
    underperformance_threshold_pct: float = 0.5,
) -> EvaluationReport:
    """Realized vs expected for `strategy_id` over the last `window_days`.

    Expected PnL = capital_allocated_usd × monthly_return_pct ×
                   (window_days / 30).

    Underperforming iff realized < expected × threshold_pct.
    """
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    pnl = await compute_realized_pnl(strategy_id, since=since)

    row = await strategies_repo.get(strategy_id)
    if row is None:
        raise strategies_repo.StrategyNotFoundError(
            f"strategy id={strategy_id} not found"
        )

    er = strategy.expected_return_for_regime(current_regime)
    monthly_expected = (
        Decimal(row.capital_allocated_usd) * Decimal(er.monthly_return_pct)
    )
    window_expected = monthly_expected * (Decimal(window_days) / Decimal(30))

    threshold = Decimal(str(underperformance_threshold_pct))
    flagged = pnl.realized_pnl_usd < window_expected * threshold

    return EvaluationReport(
        strategy_id=strategy_id,
        current_regime=current_regime,
        window_days=window_days,
        expected_pnl_usd=window_expected,
        realized_pnl_usd=pnl.realized_pnl_usd,
        is_underperforming=bool(flagged),
        threshold_pct=underperformance_threshold_pct,
    )
