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
def test_auto_approve_flips_eligible_pending(env_for_postgres, monkeypatch):
    """When AUTO_APPROVE_AFTER_SECONDS is set, pending proposals older than
    the window flip to approved (not expired) and submit_order is enqueued."""
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.execution.proposal_sweeper import auto_approve_pending

    monkeypatch.setenv("AUTO_APPROVE_AFTER_SECONDS", "60")

    captured = []
    monkeypatch.setattr(
        "trading_sandwich.execution.worker.submit_order",
        type("StubTask", (), {"delay": classmethod(lambda cls, pid: captured.append(pid))}),
    )

    async def _flow():
        factory = get_session_factory()
        now = datetime.now(timezone.utc)
        sid = uuid4()
        did = uuid4()
        pid = uuid4()
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
                proposal_id=pid, decision_id=did, signal_id=sid,
                symbol="BTCUSDT", side="long", order_type="market",
                size_usd=Decimal("500"), limit_price=None,
                stop_loss={"kind": "fixed_price", "value": "67000"},
                take_profit=None, time_in_force="GTC",
                opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
                alignment="a" * 40, similar_trades_evidence="b" * 80,
                expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("1"),
                similar_signals_count=0, status="pending",
                proposed_at=now - timedelta(seconds=120),  # 2 min old
                expires_at=now + timedelta(minutes=15),    # not yet expired
                policy_version="test",
            ))
            await session.commit()

        n = await auto_approve_pending()
        assert n == 1
        assert str(pid) in captured

        async with factory() as session:
            row = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert row.status == "approved"
            assert row.approved_by == "auto-approve"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())


@pytest.mark.integration
def test_auto_approve_skips_when_env_unset(env_for_postgres, monkeypatch):
    """When AUTO_APPROVE_AFTER_SECONDS is unset, auto_approve_pending is a no-op."""
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.execution.proposal_sweeper import auto_approve_pending

    monkeypatch.delenv("AUTO_APPROVE_AFTER_SECONDS", raising=False)

    async def _flow():
        factory = get_session_factory()
        now = datetime.now(timezone.utc)
        sid = uuid4()
        did = uuid4()
        pid = uuid4()
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
                proposal_id=pid, decision_id=did, signal_id=sid,
                symbol="BTCUSDT", side="long", order_type="market",
                size_usd=Decimal("500"), limit_price=None,
                stop_loss={"kind": "fixed_price", "value": "67000"},
                take_profit=None, time_in_force="GTC",
                opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
                alignment="a" * 40, similar_trades_evidence="b" * 80,
                expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("1"),
                similar_signals_count=0, status="pending",
                proposed_at=now - timedelta(seconds=120),
                expires_at=now + timedelta(minutes=15),
                policy_version="test",
            ))
            await session.commit()

        n = await auto_approve_pending()
        assert n == 0

        async with factory() as session:
            row = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert row.status == "pending"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
