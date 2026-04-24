import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

_REQUIRED_NON_NULL = [
    "close_price", "ema_21", "rsi_14", "atr_14",
    "ema_8", "ema_55",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
    "trend_regime", "vol_regime",
]


def _seed_candles(async_url: str, n: int = 250) -> datetime:
    """Seed n 5m candles rising linearly from 100 to 100+n*0.5."""
    base = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)

    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                for i in range(n):
                    c = 100.0 + i * 0.5
                    # Vary volume slightly so rolling std > 0 and volume_zscore is defined.
                    v = 10.0 + (i % 7) * 0.5
                    ot = base + timedelta(minutes=5 * i)
                    ct = ot + timedelta(minutes=5)
                    await conn.execute(text(
                        "INSERT INTO raw_candles "
                        "(symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                        "VALUES (:s,:tf,:ot,:ct,:o,:h,:l,:c,:v)"
                    ), {"s": "BTCUSDT", "tf": "5m", "ot": ot, "ct": ct,
                        "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c, "v": v})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base


def _latest_features(async_url: str) -> dict:
    async def _run() -> dict:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                cols = ", ".join(_REQUIRED_NON_NULL)
                row = (await conn.execute(text(
                    f"SELECT {cols} FROM features "
                    "WHERE symbol='BTCUSDT' AND timeframe='5m' "
                    "ORDER BY close_time DESC LIMIT 1"
                ))).one()
                return {k: getattr(row, k) for k in _REQUIRED_NON_NULL}
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_features_row_populates_all_phase_1_columns(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)

        command.upgrade(Config("alembic.ini"), "head")
        base = _seed_candles(pg_url, n=250)

        from trading_sandwich.features.worker import compute_features
        close_iso = (base + timedelta(minutes=5 * 250)).isoformat()
        compute_features.run("BTCUSDT", "5m", close_iso)

        row = _latest_features(pg_url)
        for col in _REQUIRED_NON_NULL:
            assert row[col] is not None, f"{col} should be non-null after 250-bar warmup"
        # Linear uptrend should classify as trend_up
        assert row["trend_regime"] == "trend_up"
