import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
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
def test_phase2_paper_e2e_signal_to_order(env_for_postgres, env_for_redis, monkeypatch):
    from trading_sandwich.celery_app import app as celery_app
    from trading_sandwich.contracts.phase2 import StopLossSpec
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import RawCandle
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import Order, TradeProposal
    from trading_sandwich.discord.approval import approve_proposal
    from trading_sandwich.mcp.tools.decisions import save_decision
    from trading_sandwich.mcp.tools.proposals import propose_trade

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {fake}")
    monkeypatch.setenv(
        "FAKE_CLAUDE_RESPONSE",
        json.dumps({"decision": "paper_trade", "rationale": "y" * 60,
                    "alert_posted": False, "proposal_created": True}),
    )

    from trading_sandwich import _policy
    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
    monkeypatch.setattr(
        _policy, "get_first_trade_size_multiplier", lambda: Decimal("1.0"),
    )

    captured = []
    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: captured.append(pid),
    )

    async def _seed():
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(RawCandle(
                symbol="BTCUSDT", timeframe="5m",
                open_time=datetime.now(timezone.utc) - timedelta(minutes=5),
                close_time=datetime.now(timezone.utc),
                open=Decimal("67900"), high=Decimal("68100"),
                low=Decimal("67800"), close=Decimal("68000"),
                volume=Decimal("100"),
            ))
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="1h",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={}, gating_outcome="claude_triaged",
                features_snapshot={"atr_14": "500"},
                detector_version="test",
            ))
            await session.commit()

        did = await save_decision(
            signal_id=sid, decision="paper_trade", rationale="y" * 60,
        )
        pid = await propose_trade(
            decision_id=did,
            symbol="BTCUSDT", side="long", order_type="market",
            size_usd=Decimal("500"), limit_price=None,
            stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67500")),
            take_profit=None,
            opportunity="o" * 80, risk="r" * 80, profit_case="p" * 80,
            alignment="a" * 40, similar_trades_evidence="s" * 80,
            expected_rr=Decimal("2.0"),
            worst_case_loss_usd=Decimal("3.68"),
            similar_signals_count=0,
        )
        await approve_proposal(pid, approver="op-1")
        return pid

    async def _assert(pid):
        factory = get_session_factory()
        async with factory() as session:
            order = (await session.execute(
                select(Order).where(Order.proposal_id == pid)
            )).scalar_one()
            assert order.status == "filled"
            assert order.execution_mode == "paper"
            prop = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert prop.status == "executed"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        pid = asyncio.run(_seed())
        assert captured == [pid]

        from trading_sandwich.execution.worker import submit_order
        submit_order.delay(str(pid))

        asyncio.run(_assert(pid))
