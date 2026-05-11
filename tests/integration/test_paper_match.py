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


@pytest.mark.integration
def test_paper_match_sell_limit_fills_on_high_not_low(env_for_postgres):
    """A direction='sell' limit (a grid selling inventory at a higher
    rung) fills when the candle HIGH reaches the limit, not the low.
    A sell-limit ABOVE the candle's high stays open; one AT/BELOW the
    high fills."""
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import RawCandle
    from trading_sandwich.db.models_phase2 import Order
    from trading_sandwich.execution.paper_match import match_async

    async def _flow():
        factory = get_session_factory()
        now = datetime.now(timezone.utc)
        async with factory() as session:
            session.add(RawCandle(
                symbol="BTCUSDT", timeframe="5m",
                open_time=now - timedelta(minutes=5), close_time=now,
                open=Decimal("100"), high=Decimal("105"),
                low=Decimal("99"), close=Decimal("101"),
                volume=Decimal("10"),
            ))
            # sell limit at 104 — high 105 >= 104 → fills
            session.add(Order(
                order_id=uuid4(), client_order_id="sell-fills",
                symbol="BTCUSDT", side="long", direction="sell",
                order_type="limit", size_usd=Decimal("6"),
                limit_price=Decimal("104"),
                stop_loss={"kind": "structural", "value": "0"},
                status="open", execution_mode="paper", policy_version="test",
            ))
            # sell limit at 110 — high 105 < 110 → stays open
            session.add(Order(
                order_id=uuid4(), client_order_id="sell-rests",
                symbol="BTCUSDT", side="long", direction="sell",
                order_type="limit", size_usd=Decimal("6"),
                limit_price=Decimal("110"),
                stop_loss={"kind": "structural", "value": "0"},
                status="open", execution_mode="paper", policy_version="test",
            ))
            # a BUY limit at 104 must NOT fill on this candle (low 99 > 104? no —
            # 99 <= 104 IS true, so a buy WOULD fill; use 98 so it stays open)
            session.add(Order(
                order_id=uuid4(), client_order_id="buy-rests",
                symbol="BTCUSDT", side="long", direction="buy",
                order_type="limit", size_usd=Decimal("6"),
                limit_price=Decimal("98"),
                stop_loss={"kind": "structural", "value": "0"},
                status="open", execution_mode="paper", policy_version="test",
            ))
            await session.commit()

        await match_async()

        async with factory() as session:
            by_coid = {
                r.client_order_id: r.status
                for r in (await session.execute(select(Order))).scalars().all()
            }
        assert by_coid["sell-fills"] == "filled"
        assert by_coid["sell-rests"] == "open"
        assert by_coid["buy-rests"] == "open"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow())
