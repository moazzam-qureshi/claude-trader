"""Detector registry. Tasks 28-34 add one entry per new detector. The signal
worker iterates this dict on every features close.
"""
from __future__ import annotations

from collections.abc import Callable

from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.signals.detectors.divergence_macd import detect_divergence_macd
from trading_sandwich.signals.detectors.divergence_rsi import detect_divergence_rsi
from trading_sandwich.signals.detectors.liquidity_sweep_daily import detect_liquidity_sweep_daily
from trading_sandwich.signals.detectors.liquidity_sweep_swing import detect_liquidity_sweep_swing
from trading_sandwich.signals.detectors.range_rejection import detect_range_rejection
from trading_sandwich.signals.detectors.squeeze_breakout import detect_squeeze_breakout
from trading_sandwich.signals.detectors.trend_pullback import detect_trend_pullback

DetectorFn = Callable[[list[FeaturesRow]], Signal | None]

REGISTRY: dict[str, DetectorFn] = {
    "trend_pullback": detect_trend_pullback,
    "squeeze_breakout": detect_squeeze_breakout,
    "divergence_rsi": detect_divergence_rsi,
    "divergence_macd": detect_divergence_macd,
    "range_rejection": detect_range_rejection,
    "liquidity_sweep_daily": detect_liquidity_sweep_daily,
    "liquidity_sweep_swing": detect_liquidity_sweep_swing,
}
