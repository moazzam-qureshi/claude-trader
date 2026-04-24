import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


def _seed_features(async_url: str) -> datetime:
    base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)

    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                for i in range(30):
                    close = 100 + i * 0.5
                    ema = close - 0.5
                    rsi = 50.0
                    if i == 27:
                        close = ema
                        rsi = 35.0
                    if i == 28:
                        rsi = 38.0
                    if i == 29:
                        close = 100 + 28 * 0.5 + 1.5
                        rsi = 42.0
                        ema = close - 0.5
                    await conn.execute(text(
                        "INSERT INTO features "
                        "(symbol,timeframe,close_time,close_price,ema_21,rsi_14,atr_14,"
                        "trend_regime,vol_regime,feature_version) "
                        "VALUES (:s,:t,:ct,:cp,:e,:r,:a,'trend_up','normal',:v)"
                    ), {"s": "BTCUSDT", "t": "1m",
                        "ct": base + timedelta(minutes=i),
                        "cp": close, "e": ema, "r": rsi, "a": 1.0, "v": "test"})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base


def _signals_rows(async_url: str) -> list[dict]:
    async def _run() -> list[dict]:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT archetype, gating_outcome, direction FROM signals"
                ))).all()
                return [{"archetype": r.archetype,
                         "gating_outcome": r.gating_outcome,
                         "direction": r.direction} for r in rows]
        finally:
            await engine.dispose()
    return asyncio.run(_run())


def _drain_outcomes_queue(redis_url: str) -> list[dict]:
    client = redis.from_url(redis_url)
    messages: list[dict] = []
    while True:
        raw = client.lpop("outcomes")
        if raw is None:
            break
        envelope = json.loads(raw)
        body = json.loads(base64.b64decode(envelope["body"]).decode("utf-8"))
        messages.append({
            "task": envelope["headers"]["task"],
            "args": body[0],
            "eta": envelope["headers"].get("eta"),
        })
    return messages


@pytest.mark.integration
def test_detect_signals_writes_row_and_schedules_outcomes(
    env_for_postgres, env_for_redis,
):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"

        env_for_redis(redis_url)
        env_for_postgres(pg_url)

        command.upgrade(Config("alembic.ini"), "head")
        base = _seed_features(pg_url)

        from trading_sandwich.signals.worker import detect_signals
        close_iso = (base + timedelta(minutes=29)).isoformat()
        detect_signals.run("BTCUSDT", "1m", close_iso)

        rows = _signals_rows(pg_url)
        # Phase 1 iterates every detector in the registry; the seeded pattern
        # may match multiple archetypes. Assert the trend_pullback row that
        # matches this pattern is persisted with claude_triaged gating.
        archetype_to_outcome = {r["archetype"]: r["gating_outcome"] for r in rows}
        assert "trend_pullback" in archetype_to_outcome
        assert archetype_to_outcome["trend_pullback"] == "claude_triaged"

        messages = _drain_outcomes_queue(redis_url)
        # One measure_outcome task per horizon (6 in Phase 1) per triaged signal.
        # If other detectors also triaged, we expect a multiple of 6.
        triaged_count = sum(1 for v in archetype_to_outcome.values() if v == "claude_triaged")
        assert len(messages) == 6 * triaged_count
        horizons = {m["args"][1] for m in messages}
        assert horizons == {"15m", "1h", "4h", "24h", "3d", "7d"}
        for m in messages:
            assert m["task"] == "trading_sandwich.outcomes.worker.measure_outcome"
