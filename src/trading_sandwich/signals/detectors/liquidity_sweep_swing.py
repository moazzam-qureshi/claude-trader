"""liquidity_sweep_swing — wick beyond trailing 20-bar swing H/L then close back.
Direction opposite the sweep. Regime-agnostic.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 30
SWING_LOOKBACK = 20


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_liquidity_sweep_swing(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    c = rows[-1]
    if c.atr_14 is None or c.swing_high_5 is None or c.swing_low_5 is None:
        return None

    window = rows[-SWING_LOOKBACK - 1:-1]
    highs = [r.swing_high_5 for r in window if r.swing_high_5 is not None]
    lows = [r.swing_low_5 for r in window if r.swing_low_5 is not None]
    if not highs or not lows:
        return None
    swing_hi = max(highs)
    swing_lo = min(lows)

    direction: str | None = None
    if c.swing_high_5 > swing_hi and c.close_price < swing_hi:
        direction = "short"
    elif c.swing_low_5 < swing_lo and c.close_price > swing_lo:
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
        archetype="liquidity_sweep_swing",
        fired_at=datetime.now(UTC),
        candle_close_time=c.close_time,
        trigger_price=c.close_price, direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "swing_high_20": float(swing_hi),
            "swing_low_20":  float(swing_lo),
        },
        gating_outcome="below_threshold",
        features_snapshot=c.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
