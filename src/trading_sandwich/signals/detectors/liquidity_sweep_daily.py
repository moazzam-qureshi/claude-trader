"""liquidity_sweep_daily detector — wick beyond prior-day H or L then close
back inside. Direction is opposite the sweep.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 30


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_liquidity_sweep_daily(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    c = rows[-1]
    if any(getattr(c, a) is None for a in ("prior_day_high", "prior_day_low",
                                           "swing_high_5", "swing_low_5",
                                           "atr_14")):
        return None

    direction: str | None = None
    if c.swing_high_5 > c.prior_day_high and c.close_price < c.prior_day_high:
        direction = "short"
    elif c.swing_low_5 < c.prior_day_low and c.close_price > c.prior_day_low:
        direction = "long"

    if direction is None:
        return None

    atr = c.atr_14
    if direction == "long":
        stop = c.swing_low_5 - atr * Decimal("0.5")
        target = c.close_price + atr * Decimal("2.5")
    else:
        stop = c.swing_high_5 + atr * Decimal("0.5")
        target = c.close_price - atr * Decimal("2.5")
    rr = abs(target - c.close_price) / abs(c.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=c.symbol, timeframe=c.timeframe,
        archetype="liquidity_sweep_daily",
        fired_at=datetime.now(UTC),
        candle_close_time=c.close_time,
        trigger_price=c.close_price, direction=direction,
        confidence=Decimal("0.75"),
        confidence_breakdown={
            "prior_day_high": float(c.prior_day_high),
            "prior_day_low":  float(c.prior_day_low),
            "wick_beyond_and_close_back": True,
        },
        gating_outcome="below_threshold",
        features_snapshot=c.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
