"""raw_funding

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_funding",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("settlement_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rate", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "settlement_time"),
    )


def downgrade() -> None:
    op.drop_table("raw_funding")
