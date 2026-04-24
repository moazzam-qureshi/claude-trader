"""raw_candles_partition

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from datetime import UTC, datetime

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE raw_candles_partitioned (
            symbol              TEXT                     NOT NULL,
            timeframe           TEXT                     NOT NULL,
            open_time           TIMESTAMP WITH TIME ZONE NOT NULL,
            close_time          TIMESTAMP WITH TIME ZONE NOT NULL,
            open                NUMERIC                  NOT NULL,
            high                NUMERIC                  NOT NULL,
            low                 NUMERIC                  NOT NULL,
            close               NUMERIC                  NOT NULL,
            volume              NUMERIC                  NOT NULL,
            quote_volume        NUMERIC,
            trade_count         INTEGER,
            taker_buy_base      NUMERIC,
            taker_buy_quote     NUMERIC,
            ingested_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, timeframe, open_time)
        ) PARTITION BY RANGE (open_time);
    """)

    now = datetime.now(UTC)
    year, month = now.year, now.month
    for offset in range(-6, 7):
        m = month + offset
        y = year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        start, end = _month_bounds(y, m)
        op.execute(
            f"CREATE TABLE raw_candles_{y:04d}_{m:02d} "
            f"PARTITION OF raw_candles_partitioned "
            f"FOR VALUES FROM ('{start}') TO ('{end}');"
        )

    op.execute("INSERT INTO raw_candles_partitioned SELECT * FROM raw_candles;")

    op.execute("""
        CREATE INDEX ix_raw_candles_symbol_tf_close_new
        ON raw_candles_partitioned (symbol, timeframe, close_time);
    """)

    op.execute("ALTER TABLE raw_candles RENAME TO raw_candles_old;")
    op.execute("ALTER TABLE raw_candles_partitioned RENAME TO raw_candles;")
    op.execute("DROP TABLE raw_candles_old;")
    op.execute("ALTER INDEX ix_raw_candles_symbol_tf_close_new RENAME TO ix_raw_candles_symbol_tf_close;")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE raw_candles_unpartitioned (
            symbol              TEXT                     NOT NULL,
            timeframe           TEXT                     NOT NULL,
            open_time           TIMESTAMP WITH TIME ZONE NOT NULL,
            close_time          TIMESTAMP WITH TIME ZONE NOT NULL,
            open                NUMERIC                  NOT NULL,
            high                NUMERIC                  NOT NULL,
            low                 NUMERIC                  NOT NULL,
            close               NUMERIC                  NOT NULL,
            volume              NUMERIC                  NOT NULL,
            quote_volume        NUMERIC,
            trade_count         INTEGER,
            taker_buy_base      NUMERIC,
            taker_buy_quote     NUMERIC,
            ingested_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, timeframe, open_time)
        );
    """)
    op.execute("INSERT INTO raw_candles_unpartitioned SELECT * FROM raw_candles;")
    op.execute("DROP TABLE raw_candles;")
    op.execute("ALTER TABLE raw_candles_unpartitioned RENAME TO raw_candles;")
    op.execute(
        "CREATE INDEX ix_raw_candles_symbol_tf_close "
        "ON raw_candles (symbol, timeframe, close_time);"
    )
