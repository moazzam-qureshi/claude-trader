"""funding_extreme detector. Counter-funding."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich._policy import get_funding_threshold
from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 3


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_funding_extreme(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    c = rows[-1]
    if c.vol_regime not in ("normal", "expansion"):
        return None
    if c.funding_rate is None or c.atr_14 is None:
        return None

    long_thr, short_thr = get_funding_threshold(c.symbol)
    direction: str | None = None
    if c.funding_rate <= long_thr:
        direction = "long"
    elif c.funding_rate >= short_thr:
        direction = "short"
    if direction is None:
        return None

    atr = c.atr_14
    if direction == "long":
        stop = c.close_price - atr * Decimal("1.5")
        target = c.close_price + atr * Decimal("3.0")
    else:
        stop = c.close_price + atr * Decimal("1.5")
        target = c.close_price - atr * Decimal("3.0")
    rr = abs(target - c.close_price) / abs(c.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=c.symbol, timeframe=c.timeframe,
        archetype="funding_extreme",
        fired_at=datetime.now(UTC),
        candle_close_time=c.close_time,
        trigger_price=c.close_price, direction=direction,
        confidence=Decimal("0.72"),
        confidence_breakdown={
            "funding_rate": float(c.funding_rate),
            "threshold_long":  float(long_thr),
            "threshold_short": float(short_thr),
        },
        gating_outcome="below_threshold",
        features_snapshot=c.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
