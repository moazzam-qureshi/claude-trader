import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_calibration_returns_medians_by_decision(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.execution.calibration import calibration_report

    async def _flow():
        factory = get_session_factory()
        async with factory() as session:
            for d, ret in [("alert", 0.02), ("alert", 0.015),
                           ("ignore", -0.005), ("ignore", -0.01)]:
                sid = uuid4()
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
                    decision_id=uuid4(), signal_id=sid, invocation_mode="triage",
                    invoked_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                    decision=d, rationale="x" * 60,
                ))
                session.add(SignalOutcome(
                    signal_id=sid, horizon="24h",
                    measured_at=datetime.now(timezone.utc),
                    close_price=Decimal("68000"),
                    return_pct=Decimal(str(ret)),
                    mfe_pct=Decimal("0.025"), mae_pct=Decimal("-0.015"),
                    stop_hit_1atr=False, target_hit_2atr=False,
                ))
            await session.commit()

        report = await calibration_report(lookback_days=30)
        assert report["alert_count"] == 2
        assert report["ignore_count"] == 2
        assert report["alert_median_24h"] > report["ignore_median_24h"]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
