"""strategies + strategy_state repo — Phase 3 plan Task 1.7.

Read/write surface for the `strategies` and `strategy_state` tables
(migration 0013). The strategy-worker (Task 1.15) drives this; the
MCP active commands (Task 1.12) deploy/wind-down through it.

Design choices:

  - State transitions go through the same state machine that lives in
    strategies.base (next_status). This guarantees the repo cannot
    drift from the ABC contract, and the DB CHECK constraint on
    strategies.status acts as a third line of defense.

  - State persistence (strategy_state) uses optimistic locking by
    `updated_at`. The worker passes its `expected_updated_at` from the
    last load; if it doesn't match the row's current updated_at, we
    raise StaleStateError. This keeps two concurrent workers from
    silently overwriting each other if duplicate task pickup happens.

  - Engine is created per-call with NullPool. Same pattern as
    settings/repo.py — testcontainer URLs change between tests and
    we don't want to pin a stale engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.strategies.base import (
    StrategyStatus,
    next_status,
)


class StaleStateError(Exception):
    """Raised when save_state's expected_updated_at doesn't match the
    DB's current updated_at — a concurrent writer beat us to it."""


class StrategyNotFoundError(Exception):
    """Raised when an operation targets a strategy_id that doesn't exist."""


@dataclass(frozen=True)
class StrategyRow:
    id: int
    strategy_type: str
    symbol: str
    status: StrategyStatus
    capital_allocated_usd: Decimal
    capital_deployed_usd: Decimal
    params: dict[str, Any]
    deployed_by: str
    deployed_at: datetime
    last_tick_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    prompt_version: str | None


@dataclass(frozen=True)
class StrategyStateRow:
    strategy_id: int
    state: dict[str, Any]
    updated_at: datetime


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


# --- create + lifecycle -----------------------------------------------------


async def create_strategy(
    *,
    strategy_type: str,
    symbol: str,
    capital_allocated_usd: Decimal,
    params: dict[str, Any],
    deployed_by: str,
    prompt_version: str | None = None,
) -> int:
    """Insert a new strategy row in `pending` status. Returns the new id."""
    engine = _engine()
    try:
        async with engine.begin() as conn:
            r = await conn.execute(
                text(
                    "INSERT INTO strategies "
                    "(strategy_type, symbol, status, capital_allocated_usd, "
                    " capital_deployed_usd, params, deployed_by, deployed_at, "
                    " prompt_version) "
                    "VALUES (:t, :s, 'pending', :cap, 0, CAST(:p AS jsonb), "
                    " :db, NOW(), :pv) "
                    "RETURNING id"
                ),
                {
                    "t": strategy_type,
                    "s": symbol,
                    "cap": capital_allocated_usd,
                    "p": json.dumps(params),
                    "db": deployed_by,
                    "pv": prompt_version,
                },
            )
            row = r.first()
            assert row is not None
            return int(row[0])
    finally:
        await engine.dispose()


async def _load_status(conn, strategy_id: int) -> StrategyStatus:
    r = await conn.execute(
        text("SELECT status FROM strategies WHERE id = :i"),
        {"i": strategy_id},
    )
    row = r.first()
    if row is None:
        raise StrategyNotFoundError(f"strategy id={strategy_id} not found")
    return StrategyStatus(row[0])


async def _transition(
    strategy_id: int,
    action: str,
    *,
    extra_columns: dict[str, Any] | None = None,
) -> None:
    """Validate the transition through the state machine, then UPDATE.

    `extra_columns` lets callers set timestamps (paused_at, completed_at)
    or error_message in the same UPDATE.
    """
    engine = _engine()
    try:
        async with engine.begin() as conn:
            current = await _load_status(conn, strategy_id)
            target = next_status(current, action)  # raises InvalidTransitionError

            sets = ["status = :ns"]
            params: dict[str, Any] = {"i": strategy_id, "ns": target.value}
            for col, val in (extra_columns or {}).items():
                sets.append(f"{col} = :{col}")
                params[col] = val

            await conn.execute(
                text(f"UPDATE strategies SET {', '.join(sets)} WHERE id = :i"),
                params,
            )
    finally:
        await engine.dispose()


async def mark_active(strategy_id: int) -> None:
    """pending → active (via the `deploy` action)."""
    await _transition(strategy_id, "deploy")


async def mark_paused(strategy_id: int) -> None:
    """active → paused. Sets paused_at."""
    await _transition(
        strategy_id, "pause",
        extra_columns={"paused_at": datetime.now(timezone.utc)},
    )


async def mark_resumed(strategy_id: int) -> None:
    """paused → active. Clears paused_at."""
    await _transition(
        strategy_id, "resume",
        extra_columns={"paused_at": None},
    )


async def mark_winding_down(strategy_id: int) -> None:
    """active|paused → winding_down."""
    await _transition(strategy_id, "wind_down")


async def mark_completed(strategy_id: int) -> None:
    """winding_down → completed. Sets completed_at."""
    await _transition(
        strategy_id, "complete",
        extra_columns={"completed_at": datetime.now(timezone.utc)},
    )


async def mark_errored(strategy_id: int, *, error_message: str) -> None:
    """any non-terminal → errored. Sets error_message."""
    await _transition(
        strategy_id, "error",
        extra_columns={"error_message": error_message},
    )


async def update_last_tick_at(strategy_id: int) -> None:
    """Bump the strategies.last_tick_at to NOW(). Called by the worker
    after a successful tick. Doesn't go through the state machine
    (last_tick_at isn't a status change)."""
    engine = _engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE strategies SET last_tick_at = NOW() WHERE id = :i"),
                {"i": strategy_id},
            )
    finally:
        await engine.dispose()


# --- state read/write -------------------------------------------------------


async def get_state(strategy_id: int) -> StrategyStateRow | None:
    """Load the persisted state JSONB for a strategy. Returns None if
    no row yet (first tick hasn't run)."""
    engine = _engine()
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text(
                    "SELECT strategy_id, state, updated_at "
                    "FROM strategy_state WHERE strategy_id = :i"
                ),
                {"i": strategy_id},
            )
            row = r.first()
            if row is None:
                return None
            return StrategyStateRow(
                strategy_id=int(row[0]), state=row[1], updated_at=row[2],
            )
    finally:
        await engine.dispose()


async def save_state(
    strategy_id: int,
    state: dict[str, Any],
    *,
    expected_updated_at: datetime | None,
) -> datetime:
    """Idempotent upsert of strategy_state with optimistic locking.

    First write: pass expected_updated_at=None.
    Subsequent writes: pass the updated_at returned by the previous
                       call (or .updated_at from get_state()).

    On race: raises StaleStateError if expected_updated_at doesn't match
    the row's current updated_at. Callers should reload + re-run their
    tick rather than retrying blindly.

    Returns the new updated_at timestamp.
    """
    engine = _engine()
    try:
        async with engine.begin() as conn:
            r = await conn.execute(
                text(
                    "SELECT updated_at FROM strategy_state WHERE strategy_id = :i"
                ),
                {"i": strategy_id},
            )
            existing = r.first()

            if existing is None:
                # First write — caller should pass expected_updated_at=None.
                if expected_updated_at is not None:
                    raise StaleStateError(
                        f"strategy {strategy_id}: caller expected updated_at="
                        f"{expected_updated_at!r} but no row exists yet"
                    )
                w = await conn.execute(
                    text(
                        "INSERT INTO strategy_state (strategy_id, state, updated_at) "
                        "VALUES (:i, CAST(:s AS jsonb), NOW()) "
                        "RETURNING updated_at"
                    ),
                    {"i": strategy_id, "s": json.dumps(state)},
                )
                return w.first()[0]

            # Existing row — must pass the current updated_at exactly.
            if existing[0] != expected_updated_at:
                raise StaleStateError(
                    f"strategy {strategy_id}: stale state, "
                    f"expected updated_at={expected_updated_at!r}, "
                    f"got {existing[0]!r}"
                )
            w = await conn.execute(
                text(
                    "UPDATE strategy_state SET "
                    "  state = CAST(:s AS jsonb), updated_at = NOW() "
                    "WHERE strategy_id = :i AND updated_at = :ex "
                    "RETURNING updated_at"
                ),
                {
                    "i": strategy_id,
                    "s": json.dumps(state),
                    "ex": expected_updated_at,
                },
            )
            row = w.first()
            if row is None:
                # Lost the race between the SELECT and UPDATE.
                raise StaleStateError(
                    f"strategy {strategy_id}: state mutated mid-update"
                )
            return row[0]
    finally:
        await engine.dispose()


# --- queries ---------------------------------------------------------------


async def list_active() -> list[StrategyRow]:
    """Return strategies the worker should still tick — active or paused.
    Excludes pending (not yet deployed), winding_down (handled by
    graceful_shutdown), completed, and errored."""
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
                    "WHERE status IN ('active','paused') "
                    "ORDER BY deployed_at"
                )
            )
            return [
                StrategyRow(
                    id=int(row[0]), strategy_type=row[1], symbol=row[2],
                    status=StrategyStatus(row[3]),
                    capital_allocated_usd=row[4], capital_deployed_usd=row[5],
                    params=row[6], deployed_by=row[7], deployed_at=row[8],
                    last_tick_at=row[9], paused_at=row[10], completed_at=row[11],
                    error_message=row[12], prompt_version=row[13],
                )
                for row in r
            ]
    finally:
        await engine.dispose()


async def get(strategy_id: int) -> StrategyRow | None:
    engine = _engine()
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text(
                    "SELECT id, strategy_type, symbol, status, "
                    "       capital_allocated_usd, capital_deployed_usd, "
                    "       params, deployed_by, deployed_at, last_tick_at, "
                    "       paused_at, completed_at, error_message, prompt_version "
                    "FROM strategies WHERE id = :i"
                ),
                {"i": strategy_id},
            )
            row = r.first()
            if row is None:
                return None
            return StrategyRow(
                id=int(row[0]), strategy_type=row[1], symbol=row[2],
                status=StrategyStatus(row[3]),
                capital_allocated_usd=row[4], capital_deployed_usd=row[5],
                params=row[6], deployed_by=row[7], deployed_at=row[8],
                last_tick_at=row[9], paused_at=row[10], completed_at=row[11],
                error_message=row[12], prompt_version=row[13],
            )
    finally:
        await engine.dispose()
