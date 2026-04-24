import asyncio
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


def _seed_candles(async_url: str) -> datetime:
    base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)

    # Phase 1 min-history enforcement: build_features_row returns None for
    # <200 bars. This test needs at least 200 bars of warmup for any row to
    # be written.
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                for i in range(250):
                    ot = base + timedelta(minutes=i)
                    ct = ot + timedelta(minutes=1)
                    px = 100 + i * 0.5
                    v = 10 + (i % 7) * 0.5  # varying volume so rolling-std > 0
                    await conn.execute(text(
                        "INSERT INTO raw_candles "
                        "(symbol, timeframe, open_time, close_time, open, high, low, close, volume) "
                        "VALUES (:s, :tf, :ot, :ct, :o, :h, :l, :c, :v)"
                    ), {"s": "BTCUSDT", "tf": "1m", "ot": ot, "ct": ct,
                        "o": px, "h": px + 0.3, "l": px - 0.3, "c": px + 0.1, "v": v})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base


def _latest_features(async_url: str) -> dict:
    async def _run() -> dict:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(
                    "SELECT close_price, ema_21, rsi_14, atr_14, feature_version "
                    "FROM features WHERE symbol='BTCUSDT' AND timeframe='1m' "
                    "ORDER BY close_time DESC LIMIT 1"
                ))
                row = result.one()
                return {
                    "close_price": row.close_price,
                    "ema_21": row.ema_21,
                    "rsi_14": row.rsi_14,
                    "atr_14": row.atr_14,
                    "feature_version": row.feature_version,
                }
        finally:
            await engine.dispose()
    return asyncio.run(_run())


def _drain_signals_queue(redis_url: str) -> list[dict]:
    """Pop every message on the 'signals' Celery queue and decode the payload."""
    client = redis.from_url(redis_url)
    messages: list[dict] = []
    while True:
        raw = client.lpop("signals")
        if raw is None:
            break
        envelope = json.loads(raw)
        body_b64 = envelope["body"]
        import base64
        body = json.loads(base64.b64decode(body_b64).decode("utf-8"))
        messages.append({
            "task": envelope["headers"]["task"],
            "args": body[0],
            "kwargs": body[1],
        })
    return messages


@pytest.mark.integration
def test_compute_features_writes_row_and_dispatches_detect_signals(
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
        base = _seed_candles(pg_url)

        from trading_sandwich.features.worker import compute_features
        close_iso = (base + timedelta(minutes=250)).isoformat()
        compute_features.run("BTCUSDT", "1m", close_iso)

        row = _latest_features(pg_url)
        assert row["close_price"] is not None
        assert row["ema_21"] is not None
        assert row["rsi_14"] is not None
        assert row["atr_14"] is not None
        assert row["feature_version"]

        messages = _drain_signals_queue(redis_url)
        assert len(messages) == 1
        msg = messages[0]
        assert msg["task"] == "trading_sandwich.signals.worker.detect_signals"
        assert msg["args"] == ["BTCUSDT", "1m", close_iso]
