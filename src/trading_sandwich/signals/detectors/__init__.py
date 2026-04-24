"""Detector registry. Tasks 28-34 add one entry per new detector. The signal
worker iterates this dict on every features close.
"""
from __future__ import annotations

from collections.abc import Callable

from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.signals.detectors.squeeze_breakout import detect_squeeze_breakout
from trading_sandwich.signals.detectors.trend_pullback import detect_trend_pullback

DetectorFn = Callable[[list[FeaturesRow]], Signal | None]

REGISTRY: dict[str, DetectorFn] = {
    "trend_pullback": detect_trend_pullback,
    "squeeze_breakout": detect_squeeze_breakout,
}
