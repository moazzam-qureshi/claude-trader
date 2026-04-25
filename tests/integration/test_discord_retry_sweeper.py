import asyncio
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import UniverseEvent


@pytest.mark.integration
def test_retry_sweeper_marks_posted_after_success(env_for_postgres, monkeypatch):
    monkeypatch.setenv("DISCORD_UNIVERSE_WEBHOOK_URL", "https://example.com/webhook")
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _seed():
            factory = get_session_factory()
            async with factory() as session:
                row = UniverseEvent(
                    occurred_at=datetime.now(timezone.utc),
                    event_type="add",
                    symbol="SUIUSDT",
                    to_tier="observation",
                    rationale="x" * 20,
                    prompt_version="abc",
                    discord_posted=False,
                )
                session.add(row)
                await session.commit()
                return row.id

        event_id = asyncio.run(_seed())

        from trading_sandwich.notifications import discord as disc

        async def _fake_post(card):
            return "msg_123"

        monkeypatch.setattr(disc, "post_card", _fake_post)

        async def _sweep():
            return await disc.retry_unposted_events(max_age_minutes=60)

        n = asyncio.run(_sweep())
        assert n == 1

        async def _verify():
            factory = get_session_factory()
            async with factory() as session:
                row = (await session.execute(
                    select(UniverseEvent).where(UniverseEvent.id == event_id)
                )).scalar_one()
                return row

        row = asyncio.run(_verify())
        assert row.discord_posted is True
        assert row.discord_message_id == "msg_123"
