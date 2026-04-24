from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from trading_sandwich.contracts.models import Signal


def _make_signal(fired_at: datetime) -> Signal:
    return Signal(
        signal_id=uuid4(),
        symbol="BTCUSDT",
        timeframe="5m",
        archetype="trend_pullback",
        fired_at=fired_at,
        candle_close_time=fired_at,
        trigger_price=Decimal("68000"),
        direction="long",
        confidence=Decimal("0.85"),
        confidence_breakdown={},
        features_snapshot={},
        detector_version="test",
    )


@pytest.mark.integration
def test_daily_cap_allows_up_to_cap(env_for_postgres, env_for_redis, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from trading_sandwich._policy import reset_cache
    from trading_sandwich.signals.gating import gate_signal_with_db

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")
        reset_cache()
        # patch the daily_triage_cap to 2 for testability
        monkeypatch.setattr(
            "trading_sandwich._policy.get_claude_daily_triage_cap",
            lambda: 2,
        )

        now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        s1 = gate_signal_with_db(_make_signal(now))
        s2 = gate_signal_with_db(_make_signal(now))
        s3 = gate_signal_with_db(_make_signal(now))
        assert s1.gating_outcome == "claude_triaged"
        assert s2.gating_outcome == "claude_triaged"
        assert s3.gating_outcome == "daily_cap_hit"
