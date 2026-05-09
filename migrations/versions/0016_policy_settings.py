"""policy_settings + policy_changes

Revision ID: 0016
Revises: 0015

Phase 3 amendment (2026-05-10): DB-backed policy settings with three-tier
mutability. See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md

policy_settings: current effective value for every Tier 2 + Tier 3 key.
  Bootstrapped from policy.yaml on first run; mutated thereafter by
  set_setting MCP (Tier 3) or /safety Discord (Tier 2). Tier 1 (halal)
  keys NEVER appear here — they live in policy.yaml only.

policy_changes: append-only audit log. Every successful mutation, every
  rejected attempt, every seed insert leaves a row. Reproduces the full
  history of any key.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "value_type IN ('int','float','string','bool','array','object')",
            name="ck_policy_settings_value_type_valid",
        ),
        sa.CheckConstraint(
            "updated_by IN ('seed','claude','operator','system')",
            name="ck_policy_settings_updated_by_valid",
        ),
    )

    op.create_table(
        "policy_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.Text(), nullable=False),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column(
            "changed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("prompt_version", sa.Text()),
        sa.CheckConstraint(
            "changed_by IN ('claude','operator','seed','system')",
            name="ck_policy_changes_changed_by_valid",
        ),
        sa.CheckConstraint(
            "authority IN ('mcp_default','operator_safety','seed','system')",
            name="ck_policy_changes_authority_valid",
        ),
        sa.CheckConstraint(
            "(applied = true) OR (rejection_reason IS NOT NULL)",
            name="ck_policy_changes_rejection_has_reason",
        ),
    )
    op.create_index(
        "ix_policy_changes_key_at",
        "policy_changes",
        ["key", "changed_at"],
        postgresql_ops={"changed_at": "DESC"},
    )
    op.create_index(
        "ix_policy_changes_at",
        "policy_changes",
        ["changed_at"],
        postgresql_ops={"changed_at": "DESC"},
    )
    op.create_index(
        "ix_policy_changes_applied",
        "policy_changes",
        ["applied", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_policy_changes_applied", table_name="policy_changes")
    op.drop_index("ix_policy_changes_at", table_name="policy_changes")
    op.drop_index("ix_policy_changes_key_at", table_name="policy_changes")
    op.drop_table("policy_changes")
    op.drop_table("policy_settings")
