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


def _seed_signal_and_candles(async_url: str) -> tuple[datetime, str]:
    base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    signal_id = uuid4()

    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO signals (signal_id,symbol,timeframe,archetype,fired_at,"
                    "candle_close_time,trigger_price,direction,confidence,confidence_breakdown,"
                    "gating_outcome,features_snapshot,stop_price,target_price,rr_ratio,detector_version) "
                    "VALUES (:id,:s,:t,:a,:f,:cct,:tp,:d,:c,"
                    "CAST('{}' AS jsonb),:go,"
                    "CAST(:snap AS jsonb),:sp,:tg,:rr,:dv)"
                ), {
                    "id": signal_id, "s": "BTCUSDT", "t": "1m", "a": "trend_pullback",
                    "f": base, "cct": base,
                    "tp": Decimal("100"), "d": "long", "c": Decimal("0.9"),
                    "go": "claude_triaged",
                    "snap": '{"atr_14": "1.0"}',
                    "sp": Decimal("99"), "tg": Decimal("102"),
                    "rr": Decimal("2"), "dv": "test",
                })
                for i in range(20):
                    ot = base + timedelta(minutes=i)
                    ct = ot + timedelta(minutes=1)
                    close = 100 + i * 0.2
                    await conn.execute(text(
                        "INSERT INTO raw_candles "
                        "(symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                        "VALUES (:s,:t,:ot,:ct,:o,:h,:l,:c,10)"
                    ), {"s": "BTCUSDT", "t": "1m", "ot": ot, "ct": ct,
                        "o": close - 0.1, "h": close + 0.3, "l": close - 0.3, "c": close})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base, str(signal_id)


def _outcome_row(async_url: str, signal_id: str) -> dict:
    async def _run() -> dict:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                row = (await conn.execute(text(
                    "SELECT horizon, stop_hit_1atr, target_hit_2atr, return_pct "
                    "FROM signal_outcomes WHERE signal_id=:id"
                ), {"id": signal_id})).one()
                return {
                    "horizon": row.horizon,
                    "stop_hit_1atr": row.stop_hit_1atr,
                    "target_hit_2atr": row.target_hit_2atr,
                    "return_pct": row.return_pct,
                }
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_measure_outcome_writes_row(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        pg_url = pg.get_connection_url()
        env_for_postgres(pg_url)

        command.upgrade(Config("alembic.ini"), "head")
        _, signal_id = _seed_signal_and_candles(pg_url)

        from trading_sandwich.outcomes.worker import measure_outcome
        measure_outcome.run(signal_id, "15m")

        row = _outcome_row(pg_url, signal_id)
        assert row["horizon"] == "15m"
        assert row["return_pct"] is not None
        # Price climbs linearly from 100 to ~103.8 over the forward window,
        # well past the 2-ATR target level of 102.
        assert row["target_hit_2atr"] is True
        assert row["stop_hit_1atr"] is False
