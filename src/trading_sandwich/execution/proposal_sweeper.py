"""Celery Beat-scheduled sweeper.

Two passes per tick:
  1. auto_approve_pending — if AUTO_APPROVE_AFTER_SECONDS is set, flip pending
     proposals older than that window to 'approved' and enqueue submit_order.
     This is the autonomous-execution path: trades fill while operator sleeps.
  2. expire_stale_proposals — flip any remaining pending proposals past
     their TTL to 'expired'. Pre-existing behavior; unchanged when
     AUTO_APPROVE_AFTER_SECONDS is unset.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import TradeProposal


def _auto_approve_seconds() -> int | None:
    raw = os.environ.get("AUTO_APPROVE_AFTER_SECONDS")
    if not raw:
        return None
    try:
        v = int(raw)
        return v if v >= 0 else None
    except ValueError:
        return None


async def auto_approve_pending() -> int:
    """Flip eligible pending proposals to approved and enqueue submit_order.

    'Eligible' = proposed_at older than AUTO_APPROVE_AFTER_SECONDS, status
    still 'pending', not yet expired. Returns count flipped.
    """
    seconds = _auto_approve_seconds()
    if seconds is None:
        return 0
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=seconds)
    approved_ids: list = []
    async with factory() as session:
        rows = (await session.execute(
            select(TradeProposal.proposal_id)
            .where(
                TradeProposal.status == "pending",
                TradeProposal.proposed_at <= cutoff,
                TradeProposal.expires_at > now,
            )
        )).scalars().all()
        if not rows:
            return 0
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id.in_(rows))
            .values(status="approved", approved_at=now, approved_by="auto-approve")
        )
        await session.commit()
        approved_ids = list(rows)

    # Enqueue submit_order for each newly-approved proposal.
    from trading_sandwich.execution.worker import submit_order
    for pid in approved_ids:
        submit_order.delay(str(pid))
    return len(approved_ids)


async def expire_stale_proposals() -> int:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        stmt = (
            update(TradeProposal)
            .where(TradeProposal.status == "pending", TradeProposal.expires_at < now)
            .values(status="expired", rejected_at=now)
            .returning(TradeProposal.proposal_id)
        )
        rows = (await session.execute(stmt)).scalars().all()
        await session.commit()
    return len(rows)


async def _sweep_async() -> int:
    """Auto-approve first (if enabled), then expire stragglers."""
    approved = await auto_approve_pending()
    expired = await expire_stale_proposals()
    return approved + expired


@app.task(name="trading_sandwich.execution.proposal_sweeper.sweep")
def sweep() -> int:
    return asyncio.run(_sweep_async())
