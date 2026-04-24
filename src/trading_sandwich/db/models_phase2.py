"""Phase 2 ORM models. All new tables land in migration 0010."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from trading_sandwich.db.models import Base


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_order_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claude_decisions.decision_id", ondelete="SET NULL")
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="SET NULL")
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_base: Mapped[Decimal | None] = mapped_column(Numeric)
    size_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    stop_loss: Mapped[dict] = mapped_column(JSONB, nullable=False)
    take_profit: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric)
    filled_base: Mapped[Decimal | None] = mapped_column(Numeric)
    fees_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)


class TradeProposal(Base):
    __tablename__ = "trade_proposals"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_trade_proposals_decision_id"),
        CheckConstraint("length(opportunity) >= 80", name="ck_trade_proposals_opportunity_len"),
        CheckConstraint("length(risk) >= 80", name="ck_trade_proposals_risk_len"),
        CheckConstraint("length(profit_case) >= 80", name="ck_trade_proposals_profit_case_len"),
        CheckConstraint("length(alignment) >= 40", name="ck_trade_proposals_alignment_len"),
        CheckConstraint(
            "length(similar_trades_evidence) >= 80",
            name="ck_trade_proposals_evidence_len",
        ),
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claude_decisions.decision_id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    stop_loss: Mapped[dict] = mapped_column(JSONB, nullable=False)
    take_profit: Mapped[dict | None] = mapped_column(JSONB)
    time_in_force: Mapped[str] = mapped_column(Text, nullable=False, server_default="GTC")

    opportunity: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(Text, nullable=False)
    profit_case: Mapped[str] = mapped_column(Text, nullable=False)
    alignment: Mapped[str] = mapped_column(Text, nullable=False)
    similar_trades_evidence: Mapped[str] = mapped_column(Text, nullable=False)

    expected_rr: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    worst_case_loss_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    similar_signals_count: Mapped[int] = mapped_column(Integer, nullable=False)
    similar_signals_win_rate: Mapped[Decimal | None] = mapped_column(Numeric)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    proposed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    executed_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)


class OrderModification(Base):
    __tablename__ = "order_modifications"
    mod_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claude_decisions.decision_id", ondelete="SET NULL")
    )
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class Position(Base):
    __tablename__ = "positions"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    size_base: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    avg_entry: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class RiskEvent(Base):
    __tablename__ = "risk_events"
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_taken: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class KillSwitchState(Base):
    __tablename__ = "kill_switch_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_kill_switch_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    tripped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    tripped_reason: Mapped[str | None] = mapped_column(Text)
    resumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    resumed_ack_reason: Mapped[str | None] = mapped_column(Text)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("signal_id", "channel", name="uq_alerts_signal_channel"),)
    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="SET NULL")
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claude_decisions.decision_id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
