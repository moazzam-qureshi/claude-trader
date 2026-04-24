"""Typed DTOs shared across workers and MCP tools.

These are the contract. A change here must accompany a test + migration
where applicable.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Horizon = Literal["15m", "1h", "4h", "24h", "3d", "7d"]
Direction = Literal["long", "short"]
Archetype = Literal[
    "trend_pullback",
    "squeeze_breakout",
    "divergence_rsi",
    "divergence_macd",
    "range_rejection",
    "liquidity_sweep_daily",
    "liquidity_sweep_swing",
    "funding_extreme",
]
GatingOutcome = Literal[
    "claude_triaged", "cooldown_suppressed", "dedup_suppressed",
    "daily_cap_hit", "below_threshold",
]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Candle(_Base):
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None
    trade_count: int | None = None
    taker_buy_base: Decimal | None = None
    taker_buy_quote: Decimal | None = None


class FeaturesRow(_Base):
    symbol: str
    timeframe: str
    close_time: datetime
    close_price: Decimal

    ema_21: Decimal | None = None
    rsi_14: Decimal | None = None
    atr_14: Decimal | None = None

    # Phase 1 extensions — all nullable
    ema_8: Decimal | None = None
    ema_55: Decimal | None = None
    ema_200: Decimal | None = None

    macd_line: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_hist: Decimal | None = None

    adx_14: Decimal | None = None
    di_plus_14: Decimal | None = None
    di_minus_14: Decimal | None = None

    stoch_rsi_k: Decimal | None = None
    stoch_rsi_d: Decimal | None = None
    roc_10: Decimal | None = None

    bb_upper: Decimal | None = None
    bb_middle: Decimal | None = None
    bb_lower: Decimal | None = None
    bb_width: Decimal | None = None

    keltner_upper: Decimal | None = None
    keltner_middle: Decimal | None = None
    keltner_lower: Decimal | None = None

    donchian_upper: Decimal | None = None
    donchian_middle: Decimal | None = None
    donchian_lower: Decimal | None = None

    obv: Decimal | None = None
    vwap: Decimal | None = None
    volume_zscore_20: Decimal | None = None
    mfi_14: Decimal | None = None

    swing_high_5: Decimal | None = None
    swing_low_5: Decimal | None = None

    pivot_p: Decimal | None = None
    pivot_r1: Decimal | None = None
    pivot_r2: Decimal | None = None
    pivot_s1: Decimal | None = None
    pivot_s2: Decimal | None = None

    prior_day_high: Decimal | None = None
    prior_day_low: Decimal | None = None
    prior_week_high: Decimal | None = None
    prior_week_low: Decimal | None = None

    funding_rate: Decimal | None = None
    funding_rate_24h_mean: Decimal | None = None

    open_interest_usd: Decimal | None = None
    oi_delta_1h: Decimal | None = None
    oi_delta_24h: Decimal | None = None

    long_short_ratio: Decimal | None = None
    ob_imbalance_05: Decimal | None = None

    ema_21_slope_bps: Decimal | None = None
    atr_percentile_100: Decimal | None = None
    bb_width_percentile_100: Decimal | None = None

    trend_regime: str | None = None
    vol_regime: str | None = None

    feature_version: str


class Signal(_Base):
    signal_id: UUID
    symbol: str
    timeframe: str
    archetype: Archetype
    fired_at: datetime
    candle_close_time: datetime
    trigger_price: Decimal
    direction: Direction

    confidence: Decimal = Field(ge=0, le=1)
    confidence_breakdown: dict

    gating_outcome: GatingOutcome = "below_threshold"
    features_snapshot: dict

    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    rr_ratio: Decimal | None = None

    detector_version: str


class Outcome(_Base):
    signal_id: UUID
    horizon: Horizon
    measured_at: datetime
    close_price: Decimal
    return_pct: Decimal
    mfe_pct: Decimal
    mae_pct: Decimal
    mfe_in_atr: Decimal | None = None
    mae_in_atr: Decimal | None = None
    stop_hit_1atr: bool
    target_hit_2atr: bool
    time_to_stop_s: int | None = None
    time_to_target_s: int | None = None
    regime_at_horizon: str | None = None
