import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _point_settings_at(url: str) -> None:
    parsed = url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = parsed.split("@", 1)
    user, password = userpass.split(":", 1)
    hostport, db = hostdb.split("/", 1)
    host, port = hostport.split(":", 1)
    os.environ["POSTGRES_USER"] = user
    os.environ["POSTGRES_PASSWORD"] = password
    os.environ["POSTGRES_DB"] = db
    os.environ["POSTGRES_HOST"] = host
    os.environ["POSTGRES_PORT"] = port
    os.environ["CELERY_BROKER_URL"] = "memory://"
    os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
    import trading_sandwich.config as cfg
    cfg._settings = None
    import trading_sandwich.db.engine as eng
    eng._engine = None
    eng._session_factory = None


@pytest.mark.integration
async def test_compute_features_writes_row():
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        _point_settings_at(url)
        await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")

        engine = create_async_engine(url)
        async with engine.begin() as conn:
            base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
            for i in range(30):
                ot = base + timedelta(minutes=i)
                ct = ot + timedelta(minutes=1)
                px = 100 + i * 0.5
                await conn.execute(text(
                    "INSERT INTO raw_candles "
                    "(symbol, timeframe, open_time, close_time, open, high, low, close, volume) "
                    "VALUES (:s, :tf, :ot, :ct, :o, :h, :l, :c, :v)"
                ), {"s": "BTCUSDT", "tf": "1m", "ot": ot, "ct": ct,
                    "o": px, "h": px + 0.3, "l": px - 0.3, "c": px + 0.1, "v": 10})

        from trading_sandwich.features.worker import compute_features
        close_iso = (base + timedelta(minutes=30)).isoformat()
        await asyncio.to_thread(compute_features.run, "BTCUSDT", "1m", close_iso)

        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT close_price, ema_21, rsi_14, atr_14, feature_version "
                "FROM features WHERE symbol='BTCUSDT' AND timeframe='1m' "
                "ORDER BY close_time DESC LIMIT 1"
            ))
            row = result.one()
            assert row.close_price is not None
            assert row.ema_21 is not None
            assert row.feature_version
        await engine.dispose()
