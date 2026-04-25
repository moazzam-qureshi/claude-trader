"""SQLAlchemy ORM for heartbeat trader tables (Phase 2.7)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_sandwich.db.models import Base


class HeartbeatShift(Base):
    __tablename__ = "heartbeat_shifts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    requested_interval_min: Mapped[int | None] = mapped_column(Integer)
    actual_interval_min: Mapped[int | None] = mapped_column(Integer)
    interval_clamped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spawned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    claude_session_id: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    tools_called: Mapped[dict | None] = mapped_column(JSONB)
    next_check_in_minutes: Mapped[int | None] = mapped_column(Integer)
    next_check_reason: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    diary_file: Mapped[str | None] = mapped_column(Text)
    state_snapshot: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)


class UniverseEvent(Base):
    __tablename__ = "universe_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    shift_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("heartbeat_shifts.id"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    from_tier: Mapped[str | None] = mapped_column(Text)
    to_tier: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reversion_criterion: Mapped[str | None] = mapped_column(Text)
    diary_ref: Mapped[str | None] = mapped_column(Text)
    discord_posted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discord_message_id: Mapped[str | None] = mapped_column(Text)
    attempted_change: Mapped[dict | None] = mapped_column(JSONB)
    blocked_by: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
