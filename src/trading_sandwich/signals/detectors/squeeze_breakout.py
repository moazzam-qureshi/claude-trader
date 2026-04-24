"""squeeze_breakout detector.

Fires when:
  - The prior few bars had vol_regime == 'squeeze'.
  - The current bar has vol_regime == 'expansion'.
  - Close has been outside the Bollinger band for the last 2 bars in the same
    direction (confirmation bar).
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 50
SQUEEZE_LOOKBACK = 5


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_squeeze_breakout(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None

    current = rows[-1]
    prev = rows[-2]

    if current.vol_regime != "expansion":
        return None

    prior_window = rows[-SQUEEZE_LOOKBACK - 2:-2]
    if not any(r.vol_regime == "squeeze" for r in prior_window):
        return None

    if any(getattr(r, attr) is None for r in (current, prev)
           for attr in ("bb_upper", "bb_lower", "atr_14")):
        return None

    direction: str | None = None
    if current.close_price > current.bb_upper and prev.close_price > prev.bb_upper:
        direction = "long"
    elif current.close_price < current.bb_lower and prev.close_price < prev.bb_lower:
        direction = "short"
    if direction is None:
        return None

    atr = current.atr_14
    if direction == "long":
        stop = current.close_price - atr * Decimal("1.5")
        target = current.close_price + atr * Decimal("3.0")
    else:
        stop = current.close_price + atr * Decimal("1.5")
        target = current.close_price - atr * Decimal("3.0")
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    confidence = Decimal("0.8")

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="squeeze_breakout",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price,
        direction=direction,
        confidence=confidence,
        confidence_breakdown={
            "squeeze_present": 0.4,
            "breakout_direction": 0.3,
            "confirmation_bar": 0.3,
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop,
        target_price=target,
        rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
