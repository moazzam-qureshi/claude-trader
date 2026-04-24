"""phase2_execution_and_proposals

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # orders (no circular FK at creation time)
    op.create_table(
        "orders",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_order_id", sa.Text, nullable=False, unique=True),
        sa.Column("exchange_order_id", sa.Text, nullable=True),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("size_base", sa.Numeric, nullable=True),
        sa.Column("size_usd", sa.Numeric, nullable=False),
        sa.Column("limit_price", sa.Numeric, nullable=True),
        sa.Column("stop_loss", postgresql.JSONB, nullable=False),
        sa.Column("take_profit", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("execution_mode", sa.Text, nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("filled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("avg_fill_price", sa.Numeric, nullable=True),
        sa.Column("filled_base", sa.Numeric, nullable=True),
        sa.Column("fees_usd", sa.Numeric, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("policy_version", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_orders_symbol_submitted", "orders", ["symbol", sa.text("submitted_at DESC")])
    op.create_index("ix_orders_status", "orders", ["status"])

    # trade_proposals
    op.create_table(
        "trade_proposals",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("size_usd", sa.Numeric, nullable=False),
        sa.Column("limit_price", sa.Numeric, nullable=True),
        sa.Column("stop_loss", postgresql.JSONB, nullable=False),
        sa.Column("take_profit", postgresql.JSONB, nullable=True),
        sa.Column("time_in_force", sa.Text, nullable=False, server_default="GTC"),

        sa.Column("opportunity", sa.Text, nullable=False),
        sa.Column("risk", sa.Text, nullable=False),
        sa.Column("profit_case", sa.Text, nullable=False),
        sa.Column("alignment", sa.Text, nullable=False),
        sa.Column("similar_trades_evidence", sa.Text, nullable=False),

        sa.Column("expected_rr", sa.Numeric, nullable=False),
        sa.Column("worst_case_loss_usd", sa.Numeric, nullable=False),
        sa.Column("similar_signals_count", sa.Integer, nullable=False),
        sa.Column("similar_signals_win_rate", sa.Numeric, nullable=True),

        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("proposed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Text, nullable=True),
        sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_version", sa.Text, nullable=False),

        sa.UniqueConstraint("decision_id", name="uq_trade_proposals_decision_id"),
        sa.CheckConstraint("length(opportunity) >= 80", name="ck_trade_proposals_opportunity_len"),
        sa.CheckConstraint("length(risk) >= 80", name="ck_trade_proposals_risk_len"),
        sa.CheckConstraint("length(profit_case) >= 80", name="ck_trade_proposals_profit_case_len"),
        sa.CheckConstraint("length(alignment) >= 40", name="ck_trade_proposals_alignment_len"),
        sa.CheckConstraint(
            "length(similar_trades_evidence) >= 80",
            name="ck_trade_proposals_evidence_len",
        ),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index("ix_trade_proposals_status", "trade_proposals", ["status"])
    op.create_index("ix_trade_proposals_expires_at", "trade_proposals", ["expires_at"])

    # Resolve circular FK: add both FKs after both tables exist.
    op.create_foreign_key(
        "fk_orders_proposal_id",
        "orders", "trade_proposals",
        ["proposal_id"], ["proposal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_trade_proposals_executed_order_id",
        "trade_proposals", "orders",
        ["executed_order_id"], ["order_id"],
        ondelete="SET NULL",
    )

    # order_modifications
    op.create_table(
        "order_modifications",
        sa.Column("mod_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "order_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("old_value", postgresql.JSONB, nullable=True),
        sa.Column("new_value", postgresql.JSONB, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("mod_id"),
    )

    # positions
    op.create_table(
        "positions",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("size_base", sa.Numeric, nullable=False),
        sa.Column("avg_entry", sa.Numeric, nullable=False),
        sa.Column("unrealized_pnl_usd", sa.Numeric, nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("symbol", "opened_at"),
    )

    # risk_events
    op.create_table(
        "risk_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("context", postgresql.JSONB, nullable=False),
        sa.Column("action_taken", sa.Text, nullable=True),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_risk_events_at", "risk_events", [sa.text("at DESC")])

    # kill_switch_state (singleton)
    op.create_table(
        "kill_switch_state",
        sa.Column("id", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("tripped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tripped_reason", sa.Text, nullable=True),
        sa.Column("resumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resumed_ack_reason", sa.Text, nullable=True),
        sa.CheckConstraint("id = 1", name="ck_kill_switch_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO kill_switch_state (id, active) VALUES (1, false)")

    # alerts
    op.create_table(
        "alerts",
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("delivered", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error", sa.Text, nullable=True),
        sa.UniqueConstraint("signal_id", "channel", name="uq_alerts_signal_channel"),
        sa.PrimaryKeyConstraint("alert_id"),
    )

    # claude_decisions indexes and UNIQUE constraint (Phase 2 usage)
    op.create_index(
        "ix_claude_decisions_signal_id", "claude_decisions", ["signal_id"]
    )
    op.create_index(
        "ix_claude_decisions_invoked_at",
        "claude_decisions",
        [sa.text("invoked_at DESC")],
    )
    op.create_index(
        "ix_claude_decisions_decision_invoked_at",
        "claude_decisions",
        ["decision", sa.text("invoked_at DESC")],
    )
    op.create_index(
        "uq_claude_decisions_signal_invocation_mode",
        "claude_decisions",
        ["signal_id", "invocation_mode"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_claude_decisions_signal_invocation_mode", table_name="claude_decisions")
    op.drop_index("ix_claude_decisions_decision_invoked_at", table_name="claude_decisions")
    op.drop_index("ix_claude_decisions_invoked_at", table_name="claude_decisions")
    op.drop_index("ix_claude_decisions_signal_id", table_name="claude_decisions")
    op.drop_table("alerts")
    op.drop_table("kill_switch_state")
    op.drop_index("ix_risk_events_at", table_name="risk_events")
    op.drop_table("risk_events")
    op.drop_table("positions")
    op.drop_table("order_modifications")
    op.drop_constraint("fk_trade_proposals_executed_order_id", "trade_proposals", type_="foreignkey")
    op.drop_constraint("fk_orders_proposal_id", "orders", type_="foreignkey")
    op.drop_index("ix_trade_proposals_expires_at", table_name="trade_proposals")
    op.drop_index("ix_trade_proposals_status", table_name="trade_proposals")
    op.drop_table("trade_proposals")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_symbol_submitted", table_name="orders")
    op.drop_table("orders")
