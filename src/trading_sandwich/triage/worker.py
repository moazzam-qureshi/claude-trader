"""triage_signal Celery task.

Spawns `claude -p`, reconciles the claude_decisions row, writes a fallback if
Claude did not write one (defense in depth).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, update

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.triage.invocation import (
    InvocationError,
    InvocationTimeout,
    invoke_claude,
)


def _workspace() -> Path:
    import os
    return Path(os.environ.get("TS_WORKSPACE", "/app"))


async def _has_decision_row(signal_id: UUID) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(ClaudeDecision)
            .where(
                ClaudeDecision.signal_id == signal_id,
                ClaudeDecision.invocation_mode == "triage",
            )
        )).scalar_one_or_none()
        return row is not None


async def _write_fallback_row(
    signal_id: UUID, reason: str, prompt_version: str | None
) -> None:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(ClaudeDecision(
            decision_id=uuid4(),
            signal_id=signal_id,
            invocation_mode="triage",
            invoked_at=now,
            completed_at=now,
            prompt_version=prompt_version,
            decision="ignore",
            rationale=("(fallback) " + reason)[:1000],
            error=reason[:500],
        ))
        await session.commit()


async def _annotate_duration(signal_id: UUID, completed: datetime, duration_ms: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(ClaudeDecision)
            .where(
                ClaudeDecision.signal_id == signal_id,
                ClaudeDecision.invocation_mode == "triage",
            )
            .values(completed_at=completed, duration_ms=duration_ms)
        )
        await session.commit()


@app.task(bind=True, name="trading_sandwich.triage.worker.triage_signal", acks_late=True)
def triage_signal(self, signal_id_str: str) -> None:
    """Invoke claude -p on the given signal. All outputs/errors land in claude_decisions."""
    signal_id = UUID(signal_id_str)
    started = datetime.now(timezone.utc)

    try:
        invoke_claude(signal_id=signal_id, workspace=_workspace())
    except InvocationTimeout as exc:
        asyncio.run(_write_fallback_row(signal_id, f"timeout: {exc}", None))
        return
    except (InvocationError, ValueError) as exc:
        asyncio.run(_write_fallback_row(signal_id, f"error: {exc}", None))
        return

    has_row = asyncio.run(_has_decision_row(signal_id))
    if not has_row:
        asyncio.run(_write_fallback_row(
            signal_id,
            "claude returned without calling save_decision",
            None,
        ))
        return

    completed = datetime.now(timezone.utc)
    duration_ms = int((completed - started).total_seconds() * 1000)
    asyncio.run(_annotate_duration(signal_id, completed, duration_ms))
