"""MCP active commands for strategies — Phase 3 plan Task 1.12.

Surface (spec §3.5):

  deploy_strategy(strategy_type, symbol, capital_usd, params, rationale)
  wind_down_strategy(strategy_id, urgency, rationale)
  pause_strategy(strategy_id, reason)
  resume_strategy(strategy_id, rationale)
  adjust_allocation(strategy_id, new_capital_usd, rationale)
  adjust_params(strategy_id, params, rationale)
  override_regime(symbol, regime, duration_hours, rationale)

Every command writes a portfolio_decisions audit row capturing the
rationale, decided_by='claude', and prompt_version=git HEAD. Errors
return as structured dicts ({status: 'error', error: ...}) so Claude
can read them and adjust — never raise to MCP.

The strategy-worker (Task 1.15) reads the persisted state on its tick
loop and acts. These commands are the WRITE side; the worker is the
READ side. Idempotent transitions are enforced by strategies.repo
(state machine) and the DB CHECK constraints.
"""
from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.mcp.server import mcp
from trading_sandwich.strategies import repo
from trading_sandwich.strategies.base import (
    InvalidTransitionError,
    Regime,
)
from trading_sandwich.strategies.regime_compat import STRATEGY_CATALOG


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def _prompt_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/app", text=True,
        ).strip()
    except Exception:
        return "unknown"


async def _record_portfolio_decision(
    *,
    decision_type: str,
    target_strategy_id: int | None,
    target_symbol: str | None,
    rationale: str,
    market_context: dict[str, Any] | None = None,
) -> None:
    """Append a portfolio_decisions audit row. Always decided_by='claude',
    prompt_version=git HEAD."""
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO portfolio_decisions "
                    "(decision_type, target_strategy_id, target_symbol, "
                    " rationale, market_context, decided_by, prompt_version) "
                    "VALUES (:dt, :tsi, :ts, :r, CAST(:mc AS jsonb), "
                    "        'claude', :pv)"
                ),
                {
                    "dt": decision_type,
                    "tsi": target_strategy_id,
                    "ts": target_symbol,
                    "r": rationale,
                    "mc": json.dumps(market_context or {}),
                    "pv": _prompt_version(),
                },
            )
    finally:
        await engine.dispose()


# --- deploy_strategy --------------------------------------------------------


@mcp.tool()
async def deploy_strategy(
    strategy_type: str,
    symbol: str,
    capital_usd: float,
    params: dict[str, Any],
    rationale: str,
) -> dict:
    """Create a new strategy in 'pending', then immediately transition
    to 'active' (the worker picks it up on next tick).

    Validates strategy_type against STRATEGY_CATALOG. capital_usd is
    cast to Decimal at the repo layer.

    Returns {status, strategy_id} on success, {status:'error', error:...}
    on failure (catalog miss, invalid params, etc.).
    """
    if strategy_type not in STRATEGY_CATALOG:
        return {
            "status": "error",
            "error": "unknown_strategy_type",
            "message": (
                f"strategy_type {strategy_type!r} not in catalog. "
                f"Valid: {sorted(STRATEGY_CATALOG)}"
            ),
        }

    sid = await repo.create_strategy(
        strategy_type=strategy_type,
        symbol=symbol,
        capital_allocated_usd=Decimal(str(capital_usd)),
        params=params,
        deployed_by="claude",
        prompt_version=_prompt_version(),
    )
    try:
        await repo.mark_active(sid)
    except InvalidTransitionError as e:
        return {"status": "error", "error": "invalid_transition", "message": str(e)}

    await _record_portfolio_decision(
        decision_type="deploy",
        target_strategy_id=sid,
        target_symbol=symbol,
        rationale=rationale,
        market_context={
            "strategy_type": strategy_type,
            "capital_usd": capital_usd,
            "params": params,
        },
    )
    return {"status": "ok", "strategy_id": sid}


# --- wind_down_strategy ----------------------------------------------------


@mcp.tool()
async def wind_down_strategy(
    strategy_id: int,
    urgency: str,
    rationale: str,
) -> dict:
    """Begin shutdown of a running strategy. urgency='graceful' cancels
    pending orders only; 'immediate' (handled by worker) market-flattens.

    Transitions active|paused → winding_down. Worker will then call the
    strategy's graceful_shutdown() / emergency_stop() and ultimately
    mark_completed() once all orders settle.
    """
    if urgency not in ("graceful", "immediate"):
        return {"status": "error", "error": "unknown_urgency",
                "message": f"urgency must be 'graceful' or 'immediate', got {urgency!r}"}

    try:
        await repo.mark_winding_down(strategy_id)
    except InvalidTransitionError as e:
        return {"status": "error", "error": "invalid_transition", "message": str(e)}
    except repo.StrategyNotFoundError as e:
        return {"status": "error", "error": "not_found", "message": str(e)}

    await _record_portfolio_decision(
        decision_type="wind_down",
        target_strategy_id=strategy_id,
        target_symbol=None,
        rationale=rationale,
        market_context={"urgency": urgency},
    )
    return {"status": "ok", "strategy_id": strategy_id, "urgency": urgency}


# --- pause / resume --------------------------------------------------------


@mcp.tool()
async def pause_strategy(strategy_id: int, reason: str) -> dict:
    """Pause an active strategy. Worker stops ticking it; state preserved."""
    try:
        await repo.mark_paused(strategy_id)
    except InvalidTransitionError as e:
        return {"status": "error", "error": "invalid_transition", "message": str(e)}
    except repo.StrategyNotFoundError as e:
        return {"status": "error", "error": "not_found", "message": str(e)}

    await _record_portfolio_decision(
        decision_type="pause",
        target_strategy_id=strategy_id,
        target_symbol=None,
        rationale=reason,
    )
    return {"status": "ok", "strategy_id": strategy_id}


@mcp.tool()
async def resume_strategy(strategy_id: int, rationale: str) -> dict:
    """Resume a paused strategy."""
    try:
        await repo.mark_resumed(strategy_id)
    except InvalidTransitionError as e:
        return {"status": "error", "error": "invalid_transition", "message": str(e)}
    except repo.StrategyNotFoundError as e:
        return {"status": "error", "error": "not_found", "message": str(e)}

    await _record_portfolio_decision(
        decision_type="resume",
        target_strategy_id=strategy_id,
        target_symbol=None,
        rationale=rationale,
    )
    return {"status": "ok", "strategy_id": strategy_id}


# --- adjust_allocation ----------------------------------------------------


@mcp.tool()
async def adjust_allocation(
    strategy_id: int,
    new_capital_usd: float,
    rationale: str,
) -> dict:
    """Change the capital allocated to an existing strategy. Does NOT
    move filled positions; the strategy's next tick adapts to the new
    cap (e.g., a grid widens its order spacing)."""
    row = await repo.get(strategy_id)
    if row is None:
        return {"status": "error", "error": "not_found",
                "message": f"strategy id={strategy_id} not found"}

    old_cap = row.capital_allocated_usd
    new_cap = Decimal(str(new_capital_usd))
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE strategies SET capital_allocated_usd = :c "
                    "WHERE id = :i"
                ),
                {"c": new_cap, "i": strategy_id},
            )
    finally:
        await engine.dispose()

    await _record_portfolio_decision(
        decision_type="adjust",
        target_strategy_id=strategy_id,
        target_symbol=row.symbol,
        rationale=rationale,
        market_context={
            "field": "capital_allocated_usd",
            "old": str(old_cap),
            "new": str(new_capital_usd),
        },
    )
    return {
        "status": "ok",
        "strategy_id": strategy_id,
        "old_capital_usd": str(old_cap),
        "new_capital_usd": str(new_capital_usd),
    }


# --- adjust_params -------------------------------------------------------


@mcp.tool()
async def adjust_params(
    strategy_id: int,
    params: dict[str, Any],
    rationale: str,
) -> dict:
    """Merge new params into the strategy's params dict (not replace).
    Lets the Strategist tune one knob without re-supplying everything."""
    row = await repo.get(strategy_id)
    if row is None:
        return {"status": "error", "error": "not_found",
                "message": f"strategy id={strategy_id} not found"}

    merged = {**row.params, **params}
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE strategies SET params = CAST(:p AS jsonb) "
                    "WHERE id = :i"
                ),
                {"p": json.dumps(merged), "i": strategy_id},
            )
    finally:
        await engine.dispose()

    await _record_portfolio_decision(
        decision_type="adjust",
        target_strategy_id=strategy_id,
        target_symbol=row.symbol,
        rationale=rationale,
        market_context={"field": "params", "patch": params},
    )
    return {"status": "ok", "strategy_id": strategy_id, "params": merged}


# --- override_regime ----------------------------------------------------


_LEGAL_REGIMES = frozenset(r.value for r in Regime)


@mcp.tool()
async def override_regime(
    symbol: str,
    regime: str,
    duration_hours: int,
    rationale: str,
) -> dict:
    """Force a regime classification on a symbol for a bounded window.

    Writes a regime_pivots row with triggered_by='claude_override'.
    Strategies acting on the regime pick this up on their next tick.

    duration_hours must be ≤ regime_classifier.manual_override_max_duration_hours
    (default 168 / 1 week). Beyond that the override should be a real
    code change, not a runtime hack.
    """
    if regime not in _LEGAL_REGIMES:
        return {
            "status": "error", "error": "unknown_regime",
            "message": (
                f"regime {regime!r} not legal. "
                f"Valid: {sorted(_LEGAL_REGIMES)}"
            ),
        }

    # Read max-duration from settings (Tier 3 — operator/Claude tunable
    # via /settings set or MCP set_setting).
    from trading_sandwich.settings import repo as settings_repo
    max_hours = await settings_repo.get(
        "regime_classifier.manual_override_max_duration_hours"
    )
    if max_hours is None:
        max_hours = 168  # spec §6.2 default
    if duration_hours > int(max_hours):
        return {
            "status": "error", "error": "duration_too_long",
            "message": (
                f"duration_hours={duration_hours} exceeds "
                f"regime_classifier.manual_override_max_duration_hours={max_hours}"
            ),
        }

    engine = _engine()
    try:
        async with engine.begin() as conn:
            # Read prior effective regime (from the most-recent pivot)
            r = await conn.execute(
                text(
                    "SELECT to_regime FROM regime_pivots "
                    "WHERE symbol = :s ORDER BY id DESC LIMIT 1"
                ),
                {"s": symbol},
            )
            row = r.first()
            prior = row[0] if row is not None else None

            await conn.execute(
                text(
                    "INSERT INTO regime_pivots "
                    "(symbol, from_regime, to_regime, triggered_by, "
                    " triggered_at, actions_taken, prompt_version) "
                    "VALUES (:s, :fr, :to, 'claude_override', NOW(), "
                    "        CAST(:at AS jsonb), :pv)"
                ),
                {
                    "s": symbol, "fr": prior, "to": regime,
                    "at": json.dumps({"duration_hours": duration_hours,
                                       "rationale": rationale}),
                    "pv": _prompt_version(),
                },
            )
    finally:
        await engine.dispose()

    await _record_portfolio_decision(
        decision_type="override",
        target_strategy_id=None,
        target_symbol=symbol,
        rationale=rationale,
        market_context={
            "regime": regime,
            "duration_hours": duration_hours,
            "from_regime": prior,
        },
    )
    return {
        "status": "ok",
        "symbol": symbol,
        "from_regime": prior,
        "to_regime": regime,
        "duration_hours": duration_hours,
    }
