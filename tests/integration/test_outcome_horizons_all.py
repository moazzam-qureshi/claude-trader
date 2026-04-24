import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _seed_signal_and_forward_candles(async_url: str) -> tuple[datetime, str]:
    """Seed 1 signal at t0 + 2016 forward 5m candles (covers 7 days)."""
    base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    signal_id = uuid4()
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO signals (signal_id,symbol,timeframe,archetype,fired_at,"
                    "candle_close_time,trigger_price,direction,confidence,confidence_breakdown,"
                    "gating_outcome,features_snapshot,stop_price,target_price,rr_ratio,detector_version)"
                    " VALUES (:id,:s,:t,:a,:f,:cct,:tp,:d,:c,CAST('{}' AS jsonb),"
                    ":go,CAST(:snap AS jsonb),:sp,:tg,:rr,:dv)"
                ), {
                    "id": signal_id, "s": "BTCUSDT", "t": "5m", "a": "trend_pullback",
                    "f": base, "cct": base,
                    "tp": Decimal("100"), "d": "long", "c": Decimal("0.9"),
                    "go": "claude_triaged",
                    "snap": '{"atr_14": "1.0"}',
                    "sp": Decimal("99"), "tg": Decimal("102"),
                    "rr": Decimal("2"), "dv": "test",
                })
                for i in range(2016):
                    ot = base + timedelta(minutes=5 * i)
                    ct = ot + timedelta(minutes=5)
                    close = 100 + min(i * 0.01, 20)
                    await conn.execute(text(
                        "INSERT INTO raw_candles (symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                        "VALUES (:s,:t,:ot,:ct,:o,:h,:l,:c,10)"
                    ), {"s": "BTCUSDT", "t": "5m", "ot": ot, "ct": ct,
                        "o": close - 0.1, "h": close + 0.3, "l": close - 0.3, "c": close})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base, str(signal_id)


def _outcome_rows(async_url: str, signal_id: str) -> list[str]:
    async def _run() -> list[str]:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT horizon FROM signal_outcomes WHERE signal_id=:id"
                ), {"id": signal_id})).all()
                return [r.horizon for r in rows]
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
@pytest.mark.timeout(180)
def test_measure_outcome_writes_all_6_horizons(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
    ):
        pg_url = pg.get_connection_url()
        env_for_postgres(pg_url)
        command.upgrade(Config("alembic.ini"), "head")

        _, signal_id = _seed_signal_and_forward_candles(pg_url)
        from trading_sandwich.outcomes.worker import measure_outcome
        for horizon in ("15m", "1h", "4h", "24h", "3d", "7d"):
            measure_outcome.run(signal_id, horizon)
        horizons = sorted(_outcome_rows(pg_url, signal_id))
        assert set(horizons) == {"15m", "1h", "4h", "24h", "3d", "7d"}
