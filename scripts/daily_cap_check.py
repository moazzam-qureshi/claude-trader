"""Check the daily-cap state — how many triages have fired today?"""
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, func
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision, Signal

async def show():
    factory = get_session_factory()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with factory() as session:
        # All decisions today
        decisions_today = (await session.execute(
            select(func.count(ClaudeDecision.decision_id))
            .where(ClaudeDecision.invoked_at >= today)
        )).scalar_one()
        print(f"claude_decisions today: {decisions_today}")

        # Signals by gating outcome
        rows = (await session.execute(
            select(Signal.gating_outcome, func.count())
            .where(Signal.fired_at >= today)
            .group_by(Signal.gating_outcome)
        )).all()
        print("\nSignals today by gating_outcome:")
        for outcome, n in rows:
            print(f"  {outcome}: {n}")

asyncio.run(show())
