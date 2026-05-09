"""portfolio_decisions

Revision ID: 0015
Revises: 0014

Phase 3 strategy pivot — see
docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md §5.3

Single decision log for the portfolio strategist persona. Every
deploy/wind_down/pause/resume/adjust/override Claude (or operator)
makes leaves a row here. Joined to strategies via target_strategy_id
when the decision is strategy-scoped; symbol-scoped decisions (e.g.,
universe-level overrides) leave target_strategy_id NULL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column(
            "target_strategy_id",
            sa.BigInteger(),
            sa.ForeignKey("strategies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_symbol", sa.Text()),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("market_context", postgresql.JSONB()),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("prompt_version", sa.Text()),
        sa.CheckConstraint(
            "decision_type IN ('deploy','wind_down','pause','resume','adjust','override','curate','alert','supervise','observe')",
            name="ck_portfolio_decisions_decision_type_valid",
        ),
        sa.CheckConstraint(
            "decided_by IN ('claude','operator','auto')",
            name="ck_portfolio_decisions_decided_by_valid",
        ),
    )
    op.create_index(
        "ix_portfolio_decisions_decided_at",
        "portfolio_decisions",
        ["decided_at"],
    )
    op.create_index(
        "ix_portfolio_decisions_strategy",
        "portfolio_decisions",
        ["target_strategy_id", "decided_at"],
    )
    op.create_index(
        "ix_portfolio_decisions_symbol",
        "portfolio_decisions",
        ["target_symbol", "decided_at"],
    )
    op.create_index(
        "ix_portfolio_decisions_type",
        "portfolio_decisions",
        ["decision_type", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_portfolio_decisions_type", table_name="portfolio_decisions")
    op.drop_index("ix_portfolio_decisions_symbol", table_name="portfolio_decisions")
    op.drop_index("ix_portfolio_decisions_strategy", table_name="portfolio_decisions")
    op.drop_index("ix_portfolio_decisions_decided_at", table_name="portfolio_decisions")
    op.drop_table("portfolio_decisions")
