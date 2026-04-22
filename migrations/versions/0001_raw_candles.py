"""raw_candles

Revision ID: 0001
Revises:
Create Date: 2026-04-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_candles",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, nullable=False),
        sa.Column("open_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("close_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric, nullable=False),
        sa.Column("high", sa.Numeric, nullable=False),
        sa.Column("low", sa.Numeric, nullable=False),
        sa.Column("close", sa.Numeric, nullable=False),
        sa.Column("volume", sa.Numeric, nullable=False),
        sa.Column("quote_volume", sa.Numeric, nullable=True),
        sa.Column("trade_count", sa.Integer, nullable=True),
        sa.Column("taker_buy_base", sa.Numeric, nullable=True),
        sa.Column("taker_buy_quote", sa.Numeric, nullable=True),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "open_time"),
    )
    op.create_index(
        "ix_raw_candles_symbol_tf_close",
        "raw_candles",
        ["symbol", "timeframe", "close_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_candles_symbol_tf_close", table_name="raw_candles")
    op.drop_table("raw_candles")
