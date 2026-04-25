import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


def _base_proposal_row(sid, did, status="pending", expires_in_min=15):
    from trading_sandwich.db.models_phase2 import TradeProposal
    now = datetime.now(timezone.utc)
    return TradeProposal(
        proposal_id=uuid4(), decision_id=did, signal_id=sid,
        symbol="BTCUSDT", side="long", order_type="limit",
        size_usd=Decimal("500"), limit_price=Decimal("68000"),
        stop_loss={"kind": "fixed_price", "value": "67500", "trigger": "mark", "working_type": "stop_market"},
        take_profit=None, time_in_force="GTC",
        opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
        alignment="a" * 40, similar_trades_evidence="b" * 80,
        expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("3.68"),
        similar_signals_count=0, similar_signals_win_rate=None,
        status=status,
        proposed_at=now,
        expires_at=now + timedelta(minutes=expires_in_min),
        policy_version="test",
    )


async def _seed_decision_and_signal(sid, did):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    factory = get_session_factory()
    async with factory() as session:
        session.add(SignalORM(
            signal_id=sid, symbol="BTCUSDT", timeframe="1h",
            archetype="trend_pullback",
            fired_at=datetime.now(timezone.utc),
            candle_close_time=datetime.now(timezone.utc),
            trigger_price=Decimal("68000"), direction="long",
            confidence=Decimal("0.85"),
            confidence_breakdown={}, gating_outcome="claude_triaged",
            features_snapshot={}, detector_version="test",
        ))
        await session.flush()
        session.add(ClaudeDecision(
            decision_id=did, signal_id=sid, invocation_mode="triage",
            invoked_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            decision="paper_trade", rationale="x" * 60,
        ))
        await session.commit()


@pytest.mark.integration
def test_approve_flips_status_and_enqueues_submit(env_for_postgres, monkeypatch):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.discord.approval import approve_proposal

    enqueued = []
    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: enqueued.append(pid),
    )

    async def _flow():
        sid = uuid4()
        did = uuid4()
        await _seed_decision_and_signal(sid, did)
        row = _base_proposal_row(sid, did)
        factory = get_session_factory()
        async with factory() as session:
            session.add(row)
            await session.commit()
            pid = row.proposal_id

        await approve_proposal(pid, approver="op-1")

        async with factory() as session:
            fresh = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert fresh.status == "approved"
            assert fresh.approved_by == "op-1"
        assert enqueued == [pid]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())


@pytest.mark.integration
def test_approve_rejects_expired_proposal(env_for_postgres, monkeypatch):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.discord.approval import ProposalExpired, approve_proposal

    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: None,
    )

    async def _flow():
        sid = uuid4()
        did = uuid4()
        await _seed_decision_and_signal(sid, did)
        row = _base_proposal_row(sid, did, expires_in_min=-1)
        factory = get_session_factory()
        async with factory() as session:
            session.add(row)
            await session.commit()
            pid = row.proposal_id

        with pytest.raises(ProposalExpired):
            await approve_proposal(pid, approver="op-1")

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())


@pytest.mark.integration
def test_approve_refuses_non_pending(env_for_postgres, monkeypatch):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.discord.approval import ProposalNotPending, approve_proposal

    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: None,
    )

    async def _flow():
        sid = uuid4()
        did = uuid4()
        await _seed_decision_and_signal(sid, did)
        row = _base_proposal_row(sid, did, status="approved")
        factory = get_session_factory()
        async with factory() as session:
            session.add(row)
            await session.commit()
            pid = row.proposal_id

        with pytest.raises(ProposalNotPending):
            await approve_proposal(pid, approver="op-1")

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
