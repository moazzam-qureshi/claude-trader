"""universe_events

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "universe_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("shift_id", sa.BigInteger(), sa.ForeignKey("heartbeat_shifts.id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("from_tier", sa.Text()),
        sa.Column("to_tier", sa.Text()),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reversion_criterion", sa.Text()),
        sa.Column("diary_ref", sa.Text()),
        sa.Column("discord_posted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("discord_message_id", sa.Text()),
        sa.Column("attempted_change", sa.JSON()),
        sa.Column("blocked_by", sa.Text()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
    )
    op.create_index("idx_events_occurred", "universe_events", ["occurred_at"])
    op.create_index("idx_events_symbol", "universe_events", ["symbol", "occurred_at"])
    op.create_index("idx_events_type", "universe_events", ["event_type", "occurred_at"])


def downgrade() -> None:
    op.drop_index("idx_events_type", table_name="universe_events")
    op.drop_index("idx_events_symbol", table_name="universe_events")
    op.drop_index("idx_events_occurred", table_name="universe_events")
    op.drop_table("universe_events")
