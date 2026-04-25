import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_sweeper_flips_expired_pending_rows(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.execution.proposal_sweeper import expire_stale_proposals

    async def _flow():
        factory = get_session_factory()
        now = datetime.now(timezone.utc)
        sid = uuid4()
        did = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="1h",
                archetype="trend_pullback", fired_at=now,
                candle_close_time=now, trigger_price=Decimal("68000"),
                direction="long", confidence=Decimal("0.85"),
                confidence_breakdown={}, gating_outcome="claude_triaged",
                features_snapshot={}, detector_version="test",
            ))
            await session.flush()
            session.add(ClaudeDecision(
                decision_id=did, signal_id=sid, invocation_mode="triage",
                invoked_at=now, completed_at=now,
                decision="paper_trade", rationale="x" * 60,
            ))
            session.add(TradeProposal(
                proposal_id=uuid4(), decision_id=did, signal_id=sid,
                symbol="BTCUSDT", side="long", order_type="limit",
                size_usd=Decimal("500"), limit_price=Decimal("68000"),
                stop_loss={}, take_profit=None, time_in_force="GTC",
                opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
                alignment="a" * 40, similar_trades_evidence="b" * 80,
                expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("1"),
                similar_signals_count=0, status="pending",
                proposed_at=now - timedelta(hours=1),
                expires_at=now - timedelta(minutes=1),
                policy_version="test",
            ))
            await session.commit()

        await expire_stale_proposals()

        async with factory() as session:
            rows = (await session.execute(
                select(TradeProposal).where(TradeProposal.signal_id == sid)
            )).scalars().all()
            assert all(r.status == "expired" for r in rows)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
