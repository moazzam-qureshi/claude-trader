import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_watchdog_writes_drift_event_when_positions_disagree(env_for_postgres):
    from sqlalchemy import select
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_phase2 import RiskEvent
    from trading_sandwich.execution.watchdog import reconcile_async

    async def _flow():
        with patch(
            "trading_sandwich.execution.watchdog._adapter_positions",
            AsyncMock(return_value=[{"symbol": "BTCUSDT", "size_base": "0.01"}]),
        ):
            await reconcile_async()
        factory = get_session_factory()
        async with factory() as session:
            events = (await session.execute(
                select(RiskEvent).where(RiskEvent.kind.like("reconcil%"))
            )).scalars().all()
            assert len(events) >= 1

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
