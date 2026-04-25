"""heartbeat_shifts

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "heartbeat_shifts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("requested_interval_min", sa.Integer()),
        sa.Column("actual_interval_min", sa.Integer()),
        sa.Column("interval_clamped", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("spawned", sa.Boolean(), nullable=False),
        sa.Column("exit_reason", sa.Text()),
        sa.Column("claude_session_id", sa.Text()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("tools_called", sa.JSON()),
        sa.Column("next_check_in_minutes", sa.Integer()),
        sa.Column("next_check_reason", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("diary_file", sa.Text()),
        sa.Column("state_snapshot", sa.Text()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
    )
    op.create_index("idx_shifts_started", "heartbeat_shifts", ["started_at"])
    op.create_index("idx_shifts_spawned", "heartbeat_shifts", ["spawned", "started_at"])


def downgrade() -> None:
    op.drop_index("idx_shifts_spawned", table_name="heartbeat_shifts")
    op.drop_index("idx_shifts_started", table_name="heartbeat_shifts")
    op.drop_table("heartbeat_shifts")
