"""MCP read tools for strategies — Phase 3 plan Task 1.11.

Surface (spec §3.5):

  list_strategies(active_only=True) -> list[dict]
  get_strategy_performance(strategy_id, since='7d') -> dict
  get_account_allocation() -> dict
  get_regime_signals(symbol) -> dict

These are how the Portfolio Strategist (Claude) reads the world before
deciding what to deploy/wind-down. Pure read paths — every mutation
goes through the active-commands tools (Task 1.12).

Decimals are serialized as strings (JSON has no Decimal; floats lose
precision on capital values). Datetimes as ISO strings.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.mcp.server import mcp
from trading_sandwich.strategies import performance, repo


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


_DURATION_RE = re.compile(r"^(\d+)([dhwm])$")
_DURATION_FACTORS = {"h": 1, "d": 24, "w": 24 * 7, "m": 24 * 30}


def _parse_since(since: str | None) -> datetime | None:
    """'7d' / '24h' / '4w' / '6m' → an absolute UTC timestamp.
    None or '' → None (entire history)."""
    if not since:
        return None
    m = _DURATION_RE.match(since)
    if m is None:
        raise ValueError(f"unparseable duration {since!r}; use NN[d|h|w|m]")
    n, unit = int(m.group(1)), m.group(2)
    hours = n * _DURATION_FACTORS[unit]
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _row_to_dict(r: repo.StrategyRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "strategy_type": r.strategy_type,
        "symbol": r.symbol,
        "status": r.status.value,
        "capital_allocated_usd": str(r.capital_allocated_usd),
        "capital_deployed_usd": str(r.capital_deployed_usd),
        "params": r.params,
        "deployed_by": r.deployed_by,
        "deployed_at": r.deployed_at.isoformat() if r.deployed_at else None,
        "last_tick_at": r.last_tick_at.isoformat() if r.last_tick_at else None,
        "paused_at": r.paused_at.isoformat() if r.paused_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "error_message": r.error_message,
        "prompt_version": r.prompt_version,
    }


@mcp.tool()
async def list_strategies(active_only: bool = True) -> list[dict]:
    """Return the strategies the Portfolio Strategist commands.

    active_only=True (default) returns active+paused (the running fleet).
    active_only=False returns ALL non-completed/non-errored strategies
    plus pending — useful when reviewing recent history.
    """
    if active_only:
        rows = await repo.list_active()
    else:
        engine = _engine()
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text(
                        "SELECT id, strategy_type, symbol, status, "
                        "       capital_allocated_usd, capital_deployed_usd, "
                        "       params, deployed_by, deployed_at, last_tick_at, "
                        "       paused_at, completed_at, error_message, prompt_version "
                        "FROM strategies "
                        "WHERE status != 'errored' "
                        "ORDER BY deployed_at"
                    )
                )
                rows = [
                    repo.StrategyRow(
                        id=int(row[0]), strategy_type=row[1], symbol=row[2],
                        status=repo.StrategyStatus(row[3]),
                        capital_allocated_usd=row[4], capital_deployed_usd=row[5],
                        params=row[6], deployed_by=row[7], deployed_at=row[8],
                        last_tick_at=row[9], paused_at=row[10], completed_at=row[11],
                        error_message=row[12], prompt_version=row[13],
                    )
                    for row in r
                ]
        finally:
            await engine.dispose()
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
async def get_strategy_performance(
    strategy_id: int,
    since: str = "7d",
) -> dict:
    """Realized PnL for a strategy over `since` (e.g. '7d', '30d', '24h').

    Returns the entry/exit breakdown, total PnL (string-encoded Decimal),
    and counts. Returns {'error': 'not_found'} for unknown strategy_id."""
    row = await repo.get(strategy_id)
    if row is None:
        return {"error": "not_found", "strategy_id": strategy_id}

    since_dt = _parse_since(since)
    pnl = await performance.compute_realized_pnl(strategy_id, since=since_dt)
    return {
        "strategy_id": strategy_id,
        "strategy_type": row.strategy_type,
        "symbol": row.symbol,
        "status": row.status.value,
        "window": since,
        "entry_cost_usd": str(pnl.entry_cost_usd),
        "exit_proceeds_usd": str(pnl.exit_proceeds_usd),
        "realized_pnl_usd": str(pnl.realized_pnl_usd),
        "entry_count": pnl.entry_count,
        "exit_count": pnl.exit_count,
    }


@mcp.tool()
async def get_account_allocation() -> dict:
    """Capital allocated to strategies that are still live (active+paused).

    Returns:
      total_allocated_usd   Decimal as string
      by_symbol             list of {symbol, allocated_usd, strategy_count}
      by_strategy_type      list of {strategy_type, allocated_usd, count}
    """
    engine = _engine()
    try:
        async with engine.connect() as conn:
            r = await conn.execute(text(
                "SELECT symbol, "
                "       SUM(capital_allocated_usd) AS allocated, "
                "       COUNT(*) AS n "
                "FROM strategies "
                "WHERE status IN ('active','paused') "
                "GROUP BY symbol "
                "ORDER BY allocated DESC"
            ))
            by_symbol = [
                {
                    "symbol": row[0],
                    "allocated_usd": str(Decimal(row[1])),
                    "strategy_count": int(row[2]),
                }
                for row in r
            ]
            r = await conn.execute(text(
                "SELECT strategy_type, "
                "       SUM(capital_allocated_usd) AS allocated, "
                "       COUNT(*) AS n "
                "FROM strategies "
                "WHERE status IN ('active','paused') "
                "GROUP BY strategy_type "
                "ORDER BY allocated DESC"
            ))
            by_strategy_type = [
                {
                    "strategy_type": row[0],
                    "allocated_usd": str(Decimal(row[1])),
                    "count": int(row[2]),
                }
                for row in r
            ]
            r = await conn.execute(text(
                "SELECT COALESCE(SUM(capital_allocated_usd), 0) "
                "FROM strategies "
                "WHERE status IN ('active','paused')"
            ))
            total = Decimal(r.first()[0])
    finally:
        await engine.dispose()
    return {
        "total_allocated_usd": str(total),
        "by_symbol": by_symbol,
        "by_strategy_type": by_strategy_type,
    }


@mcp.tool()
async def get_regime_signals(symbol: str) -> dict:
    """The regime classifier's current view of a symbol.

    Returns:
      symbol                  echoed back
      latest_regime           the raw classification on the most recent row
      effective_regime        post-hysteresis effective regime (what
                              strategies act on); None until a 2-run clears
      recent_classifications  list of {timeframe, regime, classified_at}
                              for last 10 rows on this symbol
      last_pivot              {from_regime, to_regime, triggered_at,
                              triggered_by} or None
    """
    from trading_sandwich.regime.strategy_classifier import (
        DEFAULT_THRESHOLDS,
        _effective_regime_from_history,
    )

    engine = _engine()
    try:
        async with engine.begin() as conn:
            r = await conn.execute(text(
                "SELECT timeframe, regime, classified_at "
                "FROM regime_classifications "
                "WHERE symbol = :s "
                "ORDER BY id DESC LIMIT 10"
            ), {"s": symbol})
            recent = [
                {
                    "timeframe": row[0],
                    "regime": row[1],
                    "classified_at": row[2].isoformat(),
                }
                for row in r
            ]

            # Effective regime via the same logic the classifier uses.
            # We pick the most recent timeframe the symbol has classifications
            # on (if any) for the effective lookup.
            effective_value: str | None = None
            if recent:
                tf = recent[0]["timeframe"]
                eff = await _effective_regime_from_history(
                    conn, symbol=symbol, timeframe=tf,
                    required=int(DEFAULT_THRESHOLDS["hysteresis_required_consecutive"]),
                )
                effective_value = eff.value if eff else None

            r = await conn.execute(text(
                "SELECT from_regime, to_regime, triggered_at, triggered_by "
                "FROM regime_pivots "
                "WHERE symbol = :s "
                "ORDER BY id DESC LIMIT 1"
            ), {"s": symbol})
            row = r.first()
            last_pivot = (
                {
                    "from_regime": row[0],
                    "to_regime": row[1],
                    "triggered_at": row[2].isoformat(),
                    "triggered_by": row[3],
                }
                if row is not None else None
            )
    finally:
        await engine.dispose()

    return {
        "symbol": symbol,
        "latest_regime": recent[0]["regime"] if recent else None,
        "effective_regime": effective_value,
        "recent_classifications": recent,
        "last_pivot": last_pivot,
    }
