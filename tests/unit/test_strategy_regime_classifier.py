"""Phase 3 plan Task 1.8 — strategy regime classifier (pure function).

Pins the deterministic rule from spec §3.3:

    if adx > adx_trend_threshold and price > ma50 > ma200 and slope > 0:
        return TREND_UP
    if adx > adx_trend_threshold and price < ma50 < ma200 and slope < 0:
        return TREND_DOWN
    if adx < adx_range_threshold and atr_pct > atr_pct_volatile_threshold:
        return RANGE_VOLATILE
    if adx < adx_range_threshold and atr_pct < atr_pct_quiet_threshold:
        return RANGE_QUIET
    return TRANSITIONING

We use ema_55 as the medium-term MA (closest available in features
stack to spec's ma50). Documented in classifier docstring.

The hysteresis + DB-logging wrapper is tested separately as integration.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.regime.strategy_classifier import (
    DEFAULT_THRESHOLDS,
    RegimeSignals,
    classify_signals,
)
from trading_sandwich.strategies.base import Regime


def _signals(
    *,
    price: float = 100.0,
    ma_med: float = 95.0,
    ma_long: float = 90.0,
    ma_med_slope_bps: float = 5.0,
    adx: float = 30.0,
    atr_pct: float = 0.025,
) -> RegimeSignals:
    return RegimeSignals(
        price=Decimal(str(price)),
        ma_medium=Decimal(str(ma_med)),
        ma_long=Decimal(str(ma_long)),
        ma_medium_slope_bps=ma_med_slope_bps,
        adx=adx,
        atr_pct=atr_pct,
    )


def test_trend_up_when_strong_adx_price_above_mas_positive_slope():
    sig = _signals(price=110, ma_med=100, ma_long=90, ma_med_slope_bps=8, adx=30)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.TREND_UP


def test_trend_down_when_strong_adx_price_below_mas_negative_slope():
    sig = _signals(price=80, ma_med=90, ma_long=100, ma_med_slope_bps=-8, adx=30)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.TREND_DOWN


def test_range_volatile_when_low_adx_high_atr_pct():
    sig = _signals(adx=15, atr_pct=0.04)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.RANGE_VOLATILE


def test_range_quiet_when_low_adx_low_atr_pct():
    sig = _signals(adx=15, atr_pct=0.010)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.RANGE_QUIET


def test_transitioning_when_adx_in_middle_band():
    """ADX between range_threshold (20) and trend_threshold (25) is the
    'transitioning' band — neither clearly trending nor clearly ranging."""
    sig = _signals(adx=22, atr_pct=0.025)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.TRANSITIONING


def test_transitioning_when_adx_high_but_mas_misaligned():
    """High ADX without consistent MA structure isn't a clean trend.
    Example: price above medium MA but medium below long MA — no clear
    direction. Fall through to TRANSITIONING."""
    sig = _signals(price=110, ma_med=100, ma_long=120, adx=30)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.TRANSITIONING


def test_transitioning_when_atr_in_middle_band():
    """Low ADX but ATR% between quiet (0.015) and volatile (0.03)
    cutoffs — ranging but not classifiably."""
    sig = _signals(adx=15, atr_pct=0.020)
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.TRANSITIONING


def test_thresholds_are_tunable():
    """Caller passes thresholds explicitly; the function is pure."""
    sig = _signals(adx=22, atr_pct=0.025)
    # With a permissive trend threshold, adx=22 now clears TREND_UP.
    relaxed = {**DEFAULT_THRESHOLDS, "adx_trend_threshold": 20}
    assert classify_signals(sig, relaxed) == Regime.TREND_UP


def test_missing_signals_returns_transitioning():
    """If MA or slope is None (warmup window, fresh symbol), refuse to
    classify and return TRANSITIONING. Strategies that need a regime
    will idle until enough data accumulates."""
    sig = RegimeSignals(
        price=Decimal("100"),
        ma_medium=None,
        ma_long=Decimal("90"),
        ma_medium_slope_bps=None,
        adx=30.0,
        atr_pct=0.03,
    )
    assert classify_signals(sig, DEFAULT_THRESHOLDS) == Regime.TRANSITIONING


def test_default_thresholds_match_spec_6_2():
    """Spec §6.2 regime_classifier block:
        adx_trend_threshold: 25
        adx_range_threshold: 20
        atr_pct_volatile_threshold: 0.03
        atr_pct_quiet_threshold: 0.015
    """
    assert DEFAULT_THRESHOLDS["adx_trend_threshold"] == 25
    assert DEFAULT_THRESHOLDS["adx_range_threshold"] == 20
    assert DEFAULT_THRESHOLDS["atr_pct_volatile_threshold"] == pytest.approx(0.03)
    assert DEFAULT_THRESHOLDS["atr_pct_quiet_threshold"] == pytest.approx(0.015)
