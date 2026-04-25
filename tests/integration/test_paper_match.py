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
def test_paper_match_fills_limit_order_when_price_crosses(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import RawCandle
    from trading_sandwich.db.models_phase2 import Order
    from trading_sandwich.execution.paper_match import match_async

    async def _flow():
        factory = get_session_factory()
        async with factory() as session:
            now = datetime.now(timezone.utc)
            session.add(RawCandle(
                symbol="BTCUSDT", timeframe="5m",
                open_time=now - timedelta(minutes=5),
                close_time=now,
                open=Decimal("67900"), high=Decimal("68100"),
                low=Decimal("67400"), close=Decimal("67500"),
                volume=Decimal("100"),
            ))
            session.add(Order(
                order_id=uuid4(),
                client_order_id="paper-x",
                symbol="BTCUSDT", side="long", order_type="limit",
                size_usd=Decimal("500"), limit_price=Decimal("67500"),
                stop_loss={"kind": "fixed_price", "value": "67000"},
                status="open", execution_mode="paper",
                policy_version="test",
            ))
            await session.commit()

        await match_async()

        async with factory() as session:
            row = (await session.execute(select(Order))).scalar_one()
            assert row.status == "filled"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
