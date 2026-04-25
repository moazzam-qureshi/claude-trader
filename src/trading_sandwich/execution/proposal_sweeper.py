"""Celery Beat-scheduled sweeper that flips stale pending proposals to expired."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import update

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import TradeProposal


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


@app.task(name="trading_sandwich.execution.proposal_sweeper.sweep")
def sweep() -> int:
    return asyncio.run(expire_stale_proposals())
