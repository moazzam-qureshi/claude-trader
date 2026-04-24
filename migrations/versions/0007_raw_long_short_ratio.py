"""raw_long_short_ratio

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_long_short_ratio",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ratio", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "captured_at"),
    )


def downgrade() -> None:
    op.drop_table("raw_long_short_ratio")
