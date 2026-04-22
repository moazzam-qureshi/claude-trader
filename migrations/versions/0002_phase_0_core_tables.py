"""phase_0_core_tables

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "features",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, nullable=False),
        sa.Column("close_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("close_price", sa.Numeric, nullable=False),
        sa.Column("ema_21", sa.Numeric, nullable=True),
        sa.Column("rsi_14", sa.Numeric, nullable=True),
        sa.Column("atr_14", sa.Numeric, nullable=True),
        sa.Column("trend_regime", sa.Text, nullable=True),
        sa.Column("vol_regime", sa.Text, nullable=True),
        sa.Column(
            "computed_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "close_time"),
    )
    op.create_index(
        "ix_features_symbol_tf_close",
        "features",
        ["symbol", "timeframe", sa.text("close_time DESC")],
    )

    op.create_table(
        "signals",
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, nullable=False),
        sa.Column("archetype", sa.Text, nullable=False),
        sa.Column("fired_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("candle_close_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("trigger_price", sa.Numeric, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric, nullable=False),
        sa.Column("confidence_breakdown", postgresql.JSONB, nullable=False),
        sa.Column("gating_outcome", sa.Text, nullable=False),
        sa.Column("features_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("stop_price", sa.Numeric, nullable=True),
        sa.Column("target_price", sa.Numeric, nullable=True),
        sa.Column("rr_ratio", sa.Numeric, nullable=True),
        sa.Column("detector_version", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("signal_id"),
    )
    op.create_index("ix_signals_symbol_fired", "signals", ["symbol", sa.text("fired_at DESC")])
    op.create_index("ix_signals_archetype_fired", "signals", ["archetype", sa.text("fired_at DESC")])
    op.create_index("ix_signals_gating_fired", "signals", ["gating_outcome", sa.text("fired_at DESC")])

    op.create_table(
        "signal_outcomes",
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("horizon", sa.Text, nullable=False),
        sa.Column("measured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("close_price", sa.Numeric, nullable=False),
        sa.Column("return_pct", sa.Numeric, nullable=False),
        sa.Column("mfe_pct", sa.Numeric, nullable=False),
        sa.Column("mae_pct", sa.Numeric, nullable=False),
        sa.Column("mfe_in_atr", sa.Numeric, nullable=True),
        sa.Column("mae_in_atr", sa.Numeric, nullable=True),
        sa.Column("stop_hit_1atr", sa.Boolean, nullable=False),
        sa.Column("target_hit_2atr", sa.Boolean, nullable=False),
        sa.Column("time_to_stop_s", sa.Integer, nullable=True),
        sa.Column("time_to_target_s", sa.Integer, nullable=True),
        sa.Column("regime_at_horizon", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("signal_id", "horizon"),
    )

    op.create_table(
        "claude_decisions",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("invocation_mode", sa.Text, nullable=False),
        sa.Column("invoked_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("prompt_version", sa.Text, nullable=True),
        sa.Column("input_context", postgresql.JSONB, nullable=True),
        sa.Column("tools_called", postgresql.JSONB, nullable=True),
        sa.Column("output", postgresql.JSONB, nullable=True),
        sa.Column("decision", sa.Text, nullable=True),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("cost_tokens_in", sa.Integer, nullable=True),
        sa.Column("cost_tokens_out", sa.Integer, nullable=True),
        sa.Column("cost_tokens_cache", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint("decision_id"),
    )


def downgrade() -> None:
    op.drop_table("claude_decisions")
    op.drop_table("signal_outcomes")
    op.drop_index("ix_signals_gating_fired", table_name="signals")
    op.drop_index("ix_signals_archetype_fired", table_name="signals")
    op.drop_index("ix_signals_symbol_fired", table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_features_symbol_tf_close", table_name="features")
    op.drop_table("features")
