"""divergence_macd detector — same rule shape as divergence_rsi but uses the
MACD histogram as the oscillator.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.signals.detectors._divergence_core import find_divergence_pair

MIN_HISTORY = 40
LOOKBACK = 40


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_divergence_macd(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    current = rows[-1]
    if current.vol_regime not in ("normal", "expansion") or current.atr_14 is None:
        return None
    if current.macd_hist is None:
        return None

    window = rows[-LOOKBACK:]
    cand_bull = find_divergence_pair(window, oscillator_attr="macd_hist", kind="low")
    cand_bear = find_divergence_pair(window, oscillator_attr="macd_hist", kind="high")

    if cand_bull is not None and current.trend_regime == "trend_down":
        return _build(current, "long", cand_bull)
    if cand_bear is not None and current.trend_regime == "trend_up":
        return _build(current, "short", cand_bear)
    return None


def _build(current: FeaturesRow, direction: str, reason: dict) -> Signal:
    atr = current.atr_14
    if direction == "long":
        stop = current.close_price - atr * Decimal("1.5")
        target = current.close_price + atr * Decimal("3.0")
    else:
        stop = current.close_price + atr * Decimal("1.5")
        target = current.close_price - atr * Decimal("3.0")
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="divergence_macd",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price, direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "earlier_price": reason["p_earlier"], "later_price": reason["p_later"],
            "earlier_macd_hist": reason["osc_earlier"], "later_macd_hist": reason["osc_later"],
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
