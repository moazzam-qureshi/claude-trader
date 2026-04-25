"""Look for the manually-fired decision on signal 4e0ec8c7-9382-496a-a088-3d9b3abd4c46"""
import asyncio
from uuid import UUID
from sqlalchemy import select
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision

async def show():
    factory = get_session_factory()
    async with factory() as session:
        target = UUID("4e0ec8c7-9382-496a-a088-3d9b3abd4c46")
        rows = (await session.execute(
            select(ClaudeDecision).where(ClaudeDecision.signal_id == target)
            .order_by(ClaudeDecision.invoked_at.desc())
        )).scalars().all()
        for r in rows:
            print(f"{r.invoked_at} {r.decision}")
            print(f"  rationale: {(r.rationale or '')[:300]}")
            print()

asyncio.run(show())
