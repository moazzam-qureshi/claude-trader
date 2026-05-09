"""regime_classifications + regime_pivots

Revision ID: 0014
Revises: 0013

Phase 3 strategy pivot — see
docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md §5.2

regime_classifications: every regime classification fired by the
deterministic classifier (whether or not it triggered a pivot — pivots
require 2 consecutive same classifications via hysteresis logic).

regime_pivots: only the moments when a regime pivot actually fires
(hysteresis cleared, or claude/operator override). Records what
strategies were affected. prompt_version captures the git HEAD when
Claude overrode.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regime_classifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("regime", sa.Text(), nullable=False),
        sa.Column("signals", postgresql.JSONB(), nullable=False),
        sa.Column(
            "classified_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "regime IN ('trend_up','trend_down','range_volatile','range_quiet','transitioning')",
            name="ck_regime_classifications_regime_valid",
        ),
    )
    op.create_index(
        "ix_regime_classifications_symbol_classified",
        "regime_classifications",
        ["symbol", "classified_at"],
        postgresql_ops={"classified_at": "DESC"},
    )
    op.create_index(
        "ix_regime_classifications_symbol_timeframe",
        "regime_classifications",
        ["symbol", "timeframe", "classified_at"],
    )

    op.create_table(
        "regime_pivots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("from_regime", sa.Text()),
        sa.Column("to_regime", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column(
            "triggered_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("actions_taken", postgresql.JSONB(), nullable=False),
        sa.Column("prompt_version", sa.Text()),
        sa.CheckConstraint(
            "triggered_by IN ('classifier_hysteresis','claude_override','operator_override')",
            name="ck_regime_pivots_triggered_by_valid",
        ),
        sa.CheckConstraint(
            "to_regime IN ('trend_up','trend_down','range_volatile','range_quiet','transitioning')",
            name="ck_regime_pivots_to_regime_valid",
        ),
    )
    op.create_index(
        "ix_regime_pivots_symbol_triggered",
        "regime_pivots",
        ["symbol", "triggered_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_regime_pivots_symbol_triggered", table_name="regime_pivots")
    op.drop_table("regime_pivots")
    op.drop_index(
        "ix_regime_classifications_symbol_timeframe",
        table_name="regime_classifications",
    )
    op.drop_index(
        "ix_regime_classifications_symbol_classified",
        table_name="regime_classifications",
    )
    op.drop_table("regime_classifications")
