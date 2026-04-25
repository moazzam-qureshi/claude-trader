import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_propose_trade_writes_row_on_valid_input(env_for_postgres):
    from trading_sandwich.contracts.phase2 import StopLossSpec
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.mcp.tools.proposals import propose_trade

    async def _flow() -> None:
        factory = get_session_factory()
        sid = uuid4()
        did = uuid4()
        async with factory() as session:
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
            await session.commit()

        pid = await propose_trade(
            decision_id=did,
            symbol="BTCUSDT", side="long", order_type="limit",
            size_usd=Decimal("500"), limit_price=Decimal("68000"),
            stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67500")),
            take_profit=None,
            opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
            alignment="a" * 40, similar_trades_evidence="b" * 80,
            expected_rr=Decimal("2.0"),
            worst_case_loss_usd=Decimal("3.68"),
            similar_signals_count=0,
        )
        async with factory() as session:
            row = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert row.status == "pending"
            assert row.opportunity == "x" * 80

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
