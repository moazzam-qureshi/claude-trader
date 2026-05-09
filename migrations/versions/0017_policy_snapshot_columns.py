"""policy_snapshot column on decision tables

Revision ID: 0017
Revises: 0016

Phase 3 amendment Task AM-3. Per
docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §5.3.

Adds policy_snapshot JSONB to claude_decisions and portfolio_decisions.
The snapshot captures the full effective settings (DB Tier 3 + Tier 2
+ inviolable Tier 1 file values) at decision time. Replaces the
implicit prompt_version → git checkout → re-read policy.yaml
reproduction chain that worked before settings were DB-backed.

Nullable to allow pre-amendment rows to coexist; new code MUST populate.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claude_decisions",
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "portfolio_decisions",
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_decisions", "policy_snapshot")
    op.drop_column("claude_decisions", "policy_snapshot")
