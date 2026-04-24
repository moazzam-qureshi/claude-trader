"""SQLAlchemy ORM models. Phase 0 subset."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RawCandle(Base):
    __tablename__ = "raw_candles"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)
    open_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    close_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    quote_volume: Mapped[Decimal | None] = mapped_column(Numeric)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    taker_buy_base: Mapped[Decimal | None] = mapped_column(Numeric)
    taker_buy_quote: Mapped[Decimal | None] = mapped_column(Numeric)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class Features(Base):
    __tablename__ = "features"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    timeframe: Mapped[str] = mapped_column(Text, primary_key=True)
    close_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)

    close_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)

    ema_21: Mapped[Decimal | None] = mapped_column(Numeric)
    rsi_14: Mapped[Decimal | None] = mapped_column(Numeric)
    atr_14: Mapped[Decimal | None] = mapped_column(Numeric)

    ema_8: Mapped[Decimal | None] = mapped_column(Numeric)
    ema_55: Mapped[Decimal | None] = mapped_column(Numeric)
    ema_200: Mapped[Decimal | None] = mapped_column(Numeric)

    macd_line: Mapped[Decimal | None] = mapped_column(Numeric)
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric)
    macd_hist: Mapped[Decimal | None] = mapped_column(Numeric)

    adx_14: Mapped[Decimal | None] = mapped_column(Numeric)
    di_plus_14: Mapped[Decimal | None] = mapped_column(Numeric)
    di_minus_14: Mapped[Decimal | None] = mapped_column(Numeric)

    stoch_rsi_k: Mapped[Decimal | None] = mapped_column(Numeric)
    stoch_rsi_d: Mapped[Decimal | None] = mapped_column(Numeric)
    roc_10: Mapped[Decimal | None] = mapped_column(Numeric)

    bb_upper: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_middle: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_lower: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_width: Mapped[Decimal | None] = mapped_column(Numeric)

    keltner_upper: Mapped[Decimal | None] = mapped_column(Numeric)
    keltner_middle: Mapped[Decimal | None] = mapped_column(Numeric)
    keltner_lower: Mapped[Decimal | None] = mapped_column(Numeric)

    donchian_upper: Mapped[Decimal | None] = mapped_column(Numeric)
    donchian_middle: Mapped[Decimal | None] = mapped_column(Numeric)
    donchian_lower: Mapped[Decimal | None] = mapped_column(Numeric)

    obv: Mapped[Decimal | None] = mapped_column(Numeric)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric)
    volume_zscore_20: Mapped[Decimal | None] = mapped_column(Numeric)
    mfi_14: Mapped[Decimal | None] = mapped_column(Numeric)

    swing_high_5: Mapped[Decimal | None] = mapped_column(Numeric)
    swing_low_5: Mapped[Decimal | None] = mapped_column(Numeric)

    pivot_p: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_r1: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_r2: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_s1: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_s2: Mapped[Decimal | None] = mapped_column(Numeric)

    prior_day_high: Mapped[Decimal | None] = mapped_column(Numeric)
    prior_day_low: Mapped[Decimal | None] = mapped_column(Numeric)
    prior_week_high: Mapped[Decimal | None] = mapped_column(Numeric)
    prior_week_low: Mapped[Decimal | None] = mapped_column(Numeric)

    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    funding_rate_24h_mean: Mapped[Decimal | None] = mapped_column(Numeric)

    open_interest_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    oi_delta_1h: Mapped[Decimal | None] = mapped_column(Numeric)
    oi_delta_24h: Mapped[Decimal | None] = mapped_column(Numeric)

    long_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    ob_imbalance_05: Mapped[Decimal | None] = mapped_column(Numeric)

    ema_21_slope_bps: Mapped[Decimal | None] = mapped_column(Numeric)
    atr_percentile_100: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_width_percentile_100: Mapped[Decimal | None] = mapped_column(Numeric)

    trend_regime: Mapped[str | None] = mapped_column(Text)
    vol_regime: Mapped[str | None] = mapped_column(Text)

    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)


class Signal(Base):
    __tablename__ = "signals"
    signal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    archetype: Mapped[str] = mapped_column(Text, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    candle_close_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    trigger_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)

    confidence: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    confidence_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False)

    gating_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    features_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    stop_price: Mapped[Decimal | None] = mapped_column(Numeric)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric)
    rr_ratio: Mapped[Decimal | None] = mapped_column(Numeric)

    detector_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="CASCADE"), primary_key=True
    )
    horizon: Mapped[str] = mapped_column(Text, primary_key=True)
    measured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    return_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    mfe_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    mae_pct: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    mfe_in_atr: Mapped[Decimal | None] = mapped_column(Numeric)
    mae_in_atr: Mapped[Decimal | None] = mapped_column(Numeric)
    stop_hit_1atr: Mapped[bool] = mapped_column(Boolean, nullable=False)
    target_hit_2atr: Mapped[bool] = mapped_column(Boolean, nullable=False)
    time_to_stop_s: Mapped[int | None] = mapped_column(Integer)
    time_to_target_s: Mapped[int | None] = mapped_column(Integer)
    regime_at_horizon: Mapped[str | None] = mapped_column(Text)


class ClaudeDecision(Base):
    __tablename__ = "claude_decisions"
    decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="SET NULL")
    )
    invocation_mode: Mapped[str] = mapped_column(Text, nullable=False)
    invoked_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    input_context: Mapped[dict | None] = mapped_column(JSONB)
    tools_called: Mapped[list | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    cost_tokens_in: Mapped[int | None] = mapped_column(Integer)
    cost_tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_tokens_cache: Mapped[int | None] = mapped_column(Integer)
