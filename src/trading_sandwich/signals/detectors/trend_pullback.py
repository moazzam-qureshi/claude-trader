"""trend_pullback detector (Phase 0 simplified version).

Rule (long only in Phase 0):
  - Close > EMA(21) on the most recent bar
  - Within the last 3 bars, a bar's close touched or dipped below EMA(21)
  - Most recent close > previous close (momentum reset confirmed)
  - RSI(14) was < 40 within the last 3 bars and is now >= 40
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 22


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_trend_pullback(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None

    current = rows[-1]
    previous = rows[-2]
    window = rows[-4:-1]

    if current.ema_21 is None or current.rsi_14 is None or current.atr_14 is None:
        return None
    if any(r.ema_21 is None or r.rsi_14 is None for r in window):
        return None

    if current.close_price <= current.ema_21:
        return None

    touched = any(r.close_price <= r.ema_21 for r in window)
    if not touched:
        return None

    close_up = current.close_price > previous.close_price
    if not close_up:
        return None

    rsi_dip = any(r.rsi_14 < Decimal("40") for r in window)
    rsi_recovered = current.rsi_14 >= Decimal("40")
    if not (rsi_dip and rsi_recovered):
        return None

    min_rsi = min(r.rsi_14 for r in window)
    confidence = Decimal("1.0") if min_rsi < Decimal("30") else Decimal("0.85")

    stop = current.close_price - (current.atr_14 * Decimal("1.5"))
    target = current.close_price + (current.atr_14 * Decimal("3.0"))
    rr = (target - current.close_price) / (current.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol,
        timeframe=current.timeframe,
        archetype="trend_pullback",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price,
        direction="long",
        confidence=confidence,
        confidence_breakdown={
            "trend_filter": 0.4,
            "rsi_cross": 0.3,
            "momentum_reset": 0.3,
            "rsi_depth_bonus": float(min_rsi),
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop,
        target_price=target,
        rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
