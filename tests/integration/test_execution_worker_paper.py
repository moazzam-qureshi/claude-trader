import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.mark.integration
def test_submit_order_paper_market_writes_filled_order(
    env_for_postgres, env_for_redis, monkeypatch,
):
    from decimal import Decimal as _D

    from trading_sandwich import _policy
    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
    monkeypatch.setattr(
        _policy, "get_first_trade_size_multiplier", lambda: _D("1.0"),
    )

    from trading_sandwich.celery_app import app as celery_app
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import RawCandle
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import Order, TradeProposal

    async def _seed_and_run():
        factory = get_session_factory()
        sid = uuid4(); did = uuid4(); pid = uuid4()
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
            await session.flush()
            session.add(ClaudeDecision(
                decision_id=did, signal_id=sid, invocation_mode="triage",
                invoked_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                decision="paper_trade", rationale="x" * 60,
            ))
            session.add(TradeProposal(
                proposal_id=pid, decision_id=did, signal_id=sid,
                symbol="BTCUSDT", side="long", order_type="market",
                size_usd=Decimal("500"), limit_price=None,
                stop_loss={"kind": "fixed_price", "value": "67000",
                           "trigger": "mark", "working_type": "stop_market"},
                take_profit=None, time_in_force="GTC",
                opportunity="o" * 80, risk="r" * 80, profit_case="p" * 80,
                alignment="a" * 40, similar_trades_evidence="s" * 80,
                expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("7.35"),
                similar_signals_count=0, status="approved",
                proposed_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                approved_at=datetime.now(timezone.utc),
                approved_by="op-1",
                policy_version="test",
            ))
            await session.commit()
        return pid

    async def _check(pid):
        factory = get_session_factory()
        async with factory() as session:
            order = (await session.execute(
                select(Order).where(Order.proposal_id == pid)
            )).scalar_one()
            assert order.status == "filled"
            assert order.execution_mode == "paper"
            assert order.avg_fill_price == Decimal("68000")
            prop = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert prop.status == "executed"
            assert prop.executed_order_id == order.order_id

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        from trading_sandwich.execution.worker import submit_order
        pid = asyncio.run(_seed_and_run())
        submit_order.delay(str(pid))
        asyncio.run(_check(pid))
