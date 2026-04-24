"""raw_open_interest

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_open_interest",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open_interest_usd", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "captured_at"),
    )
    op.create_index(
        "ix_oi_symbol_captured_desc",
        "raw_open_interest",
        ["symbol", sa.text("captured_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_oi_symbol_captured_desc", table_name="raw_open_interest")
    op.drop_table("raw_open_interest")
