import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.mark.integration
def test_approval_loop_end_to_end(env_for_postgres, env_for_redis, monkeypatch):
    from trading_sandwich.celery_app import app as celery_app
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.discord.approval import approve_proposal

    enqueued = []
    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: enqueued.append(pid),
    )

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {fake}")
    monkeypatch.setenv(
        "FAKE_CLAUDE_RESPONSE",
        json.dumps({
            "decision": "paper_trade",
            "rationale": "y" * 60,
            "alert_posted": False,
            "proposal_created": True,
        }),
    )

    async def _seed_signal_and_simulate_tools():
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="1h",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={"atr_14": "500"},
                detector_version="test",
            ))
            await session.commit()

        from trading_sandwich.contracts.phase2 import StopLossSpec
        from trading_sandwich.mcp.tools.decisions import save_decision
        from trading_sandwich.mcp.tools.proposals import propose_trade

        did = await save_decision(
            signal_id=sid, decision="paper_trade", rationale="y" * 60,
        )
        pid = await propose_trade(
            decision_id=did,
            symbol="BTCUSDT", side="long", order_type="limit",
            size_usd=Decimal("500"), limit_price=Decimal("68000"),
            stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67500")),
            take_profit=None,
            opportunity="o" * 80, risk="r" * 80, profit_case="p" * 80,
            alignment="a" * 40, similar_trades_evidence="s" * 80,
            expected_rr=Decimal("2.0"),
            worst_case_loss_usd=Decimal("3.68"),
            similar_signals_count=0,
        )
        return sid, pid

    async def _assert_approved(pid):
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert row.status == "approved"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        sid, pid = asyncio.run(_seed_signal_and_simulate_tools())

        asyncio.run(approve_proposal(pid, approver="op-1"))

        asyncio.run(_assert_approved(pid))
        assert enqueued == [pid]
