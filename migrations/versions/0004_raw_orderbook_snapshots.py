"""raw_orderbook_snapshots

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_orderbook_snapshots",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("bids", postgresql.JSONB, nullable=False),
        sa.Column("asks", postgresql.JSONB, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "captured_at"),
    )
    op.create_index(
        "ix_ob_snapshots_symbol_captured_desc",
        "raw_orderbook_snapshots",
        ["symbol", sa.text("captured_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_ob_snapshots_symbol_captured_desc", table_name="raw_orderbook_snapshots")
    op.drop_table("raw_orderbook_snapshots")
