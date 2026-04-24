import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _assert_tables(async_url: str, tables: list[str]) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                for tbl in tables:
                    result = await conn.execute(text(f"SELECT to_regclass('public.{tbl}')"))
                    assert result.scalar() == tbl, f"{tbl} missing"
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_migrations_run_and_create_raw_candles(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(url, ["raw_candles"])


@pytest.mark.integration
def test_all_phase_0_tables_exist(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(
            url, ["raw_candles", "features", "signals", "signal_outcomes", "claude_decisions"]
        )


_PHASE_1_FEATURES_COLUMNS = [
    "ema_8", "ema_55", "ema_200",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "swing_high_5", "swing_low_5",
    "pivot_p", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2",
    "prior_day_high", "prior_day_low", "prior_week_high", "prior_week_low",
    "funding_rate", "funding_rate_24h_mean",
    "open_interest_usd", "oi_delta_1h", "oi_delta_24h",
    "long_short_ratio", "ob_imbalance_05",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
]


@pytest.mark.integration
def test_features_has_phase_1_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _assert_cols() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    rows = (await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='features'"
                    ))).scalars().all()
                    for col in _PHASE_1_FEATURES_COLUMNS:
                        assert col in rows, f"column {col} missing from features"
            finally:
                await engine.dispose()
        asyncio.run(_assert_cols())
