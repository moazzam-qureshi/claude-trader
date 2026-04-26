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
    approved_proposals: list = []
    async with factory() as session:
        rows = (await session.execute(
            select(TradeProposal)
            .where(
                TradeProposal.status == "pending",
                TradeProposal.proposed_at <= cutoff,
                TradeProposal.expires_at > now,
            )
        )).scalars().all()
        if not rows:
            return 0
        approved_proposals = [
            {"id": r.proposal_id, "symbol": r.symbol, "side": r.side,
             "size_usd": float(r.size_usd)}
            for r in rows
        ]
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id.in_([r.proposal_id for r in rows]))
            .values(status="approved", approved_at=now, approved_by="auto-approve")
        )
        await session.commit()

    # Discord: announce each approval (Phase 2.7)
    from trading_sandwich.notifications.discord import (
        post_card_safe, render_proposal_approved_card,
    )
    for p in approved_proposals:
        await post_card_safe(render_proposal_approved_card(
            occurred_at=now, symbol=p["symbol"], side=p["side"],
            size_usd=p["size_usd"], auto=True,
        ))

    # Enqueue submit_order for each newly-approved proposal.
    from trading_sandwich.execution.worker import submit_order
    for p in approved_proposals:
        submit_order.delay(str(p["id"]))
    return len(approved_proposals)


async def expire_stale_proposals() -> int:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        # Fetch first so we can announce, then update.
        to_expire = (await session.execute(
            select(TradeProposal)
            .where(TradeProposal.status == "pending", TradeProposal.expires_at < now)
        )).scalars().all()
        expired_meta = [
            {"id": r.proposal_id, "symbol": r.symbol, "side": r.side,
             "size_usd": float(r.size_usd), "expires_at": r.expires_at}
            for r in to_expire
        ]
        if not expired_meta:
            return 0
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id.in_([r.proposal_id for r in to_expire]))
            .values(status="expired", rejected_at=now)
        )
        await session.commit()

    # Discord: announce each expiry (Phase 2.7)
    from trading_sandwich.notifications.discord import (
        post_card_safe, render_proposal_expired_card,
    )
    for p in expired_meta:
        await post_card_safe(render_proposal_expired_card(
            occurred_at=now, symbol=p["symbol"], side=p["side"],
            size_usd=p["size_usd"], expires_at=p["expires_at"],
        ))
    return len(expired_meta)


async def _sweep_async() -> int:
    """Auto-approve first (if enabled), then expire stragglers."""
    approved = await auto_approve_pending()
    expired = await expire_stale_proposals()
    return approved + expired


@app.task(name="trading_sandwich.execution.proposal_sweeper.sweep")
def sweep() -> int:
    return asyncio.run(_sweep_async())
