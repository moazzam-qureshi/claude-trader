"""range_rejection detector.

Fires only in trend_regime=range + vol_regime=normal. Wick-touch-and-close-back
at either Donchian boundary.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 50


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_range_rejection(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None

    current = rows[-1]
    if current.trend_regime != "range" or current.vol_regime != "normal":
        return None
    if any(getattr(current, a) is None
           for a in ("donchian_upper", "donchian_lower", "atr_14",
                     "swing_high_5", "swing_low_5")):
        return None

    direction: str | None = None
    if (
        current.swing_low_5 <= current.donchian_lower
        and current.close_price > current.donchian_lower
    ):
        direction = "long"
    elif (
        current.swing_high_5 >= current.donchian_upper
        and current.close_price < current.donchian_upper
    ):
        direction = "short"

    if direction is None:
        return None

    atr = current.atr_14
    if direction == "long":
        stop = current.swing_low_5 - atr * Decimal("0.5")
        target = current.donchian_upper
    else:
        stop = current.swing_high_5 + atr * Decimal("0.5")
        target = current.donchian_lower
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="range_rejection",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price, direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "donchian_upper": float(current.donchian_upper),
            "donchian_lower": float(current.donchian_lower),
            "wick_below_low": direction == "long",
            "wick_above_high": direction == "short",
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
