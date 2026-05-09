"""strategies + strategy_state + strategy_orders

Revision ID: 0013
Revises: 0012

Phase 3 strategy pivot — see
docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md §5.1

DEVIATION FROM SPEC: spec §5.1 declares
    strategy_orders.order_id BIGINT REFERENCES orders(id)
The existing orders table (migration 0010) actually uses
    orders.order_id UUID PRIMARY KEY
There is no orders.id column. Migration here uses
    strategy_orders.order_id UUID REFERENCES orders(order_id)
to match reality. Semantic intent (link strategy-placed order to the
canonical orders row) is preserved. Spec amendment recorded in
docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md
under "Amendments" appendix.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("capital_allocated_usd", sa.Numeric(), nullable=False),
        sa.Column("capital_deployed_usd", sa.Numeric(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("deployed_by", sa.Text(), nullable=False),
        sa.Column("deployed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_tick_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("paused_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("prompt_version", sa.Text()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','paused','winding_down','completed','errored')",
            name="ck_strategies_status_valid",
        ),
        sa.CheckConstraint(
            "deployed_by IN ('claude','operator','system')",
            name="ck_strategies_deployed_by_valid",
        ),
    )
    op.create_index(
        "ix_strategies_active_unique",
        "strategies",
        ["strategy_type", "symbol"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','paused')"),
    )
    op.create_index(
        "ix_strategies_status",
        "strategies",
        ["status", "deployed_at"],
    )
    op.create_index(
        "ix_strategies_symbol",
        "strategies",
        ["symbol", "status"],
    )

    op.create_table(
        "strategy_state",
        sa.Column(
            "strategy_id",
            sa.BigInteger(),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "strategy_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "strategy_id",
            sa.BigInteger(),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("grid_level", sa.Integer()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("strategy_id", "order_id", name="uq_strategy_orders_pair"),
    )
    op.create_index(
        "ix_strategy_orders_strategy",
        "strategy_orders",
        ["strategy_id", "created_at"],
    )
    op.create_index(
        "ix_strategy_orders_order",
        "strategy_orders",
        ["order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_orders_order", table_name="strategy_orders")
    op.drop_index("ix_strategy_orders_strategy", table_name="strategy_orders")
    op.drop_table("strategy_orders")
    op.drop_table("strategy_state")
    op.drop_index("ix_strategies_symbol", table_name="strategies")
    op.drop_index("ix_strategies_status", table_name="strategies")
    op.drop_index("ix_strategies_active_unique", table_name="strategies")
    op.drop_table("strategies")
