"""Rule-based regime classifier. Maps per-candle indicator values to two
independent labels: trend_regime in {trend_up, trend_down, range} and
vol_regime in {squeeze, normal, expansion}.

Thresholds come from `policy.yaml` so they're tunable without code changes.
The Phase 1 defaults are deliberately conservative; tune once >=2 weeks of
live data accumulate.
"""
from __future__ import annotations

from decimal import Decimal


def classify(
    *,
    close: Decimal | None,
    ema_55: Decimal | None,
    ema_slope_bps: float | None,
    adx: float | None,
    bb_width_percentile_100: float | None,
    policy: dict,
) -> tuple[str, str]:
    """Return (trend_regime, vol_regime) for one candle.

    Falls back to ('range', 'normal') when any input needed for a label is None
    (warmup periods, missing microstructure, etc.). This keeps the downstream
    detector gating deterministic: untyped candles get the most conservative
    label.
    """
    trend = _classify_trend(
        close=close, ema_55=ema_55,
        ema_slope_bps=ema_slope_bps, adx=adx,
        policy=policy,
    )
    vol = _classify_vol(
        bb_width_percentile_100=bb_width_percentile_100,
        policy=policy,
    )
    return trend, vol


def _classify_trend(
    *,
    close: Decimal | None, ema_55: Decimal | None,
    ema_slope_bps: float | None, adx: float | None,
    policy: dict,
) -> str:
    if close is None or ema_55 is None or ema_slope_bps is None or adx is None:
        return "range"

    slope_threshold = float(policy["trend_slope_threshold_bps"])
    adx_threshold = float(policy["adx_trend_threshold"])

    if adx < adx_threshold:
        return "range"

    if close > ema_55 and ema_slope_bps > slope_threshold:
        return "trend_up"
    if close < ema_55 and ema_slope_bps < -slope_threshold:
        return "trend_down"
    return "range"


def _classify_vol(
    *,
    bb_width_percentile_100: float | None,
    policy: dict,
) -> str:
    if bb_width_percentile_100 is None:
        return "normal"

    squeeze_cutoff = float(policy["squeeze_percentile"])
    expansion_cutoff = float(policy["expansion_percentile"])

    if bb_width_percentile_100 < squeeze_cutoff:
        return "squeeze"
    if bb_width_percentile_100 > expansion_cutoff:
        return "expansion"
    return "normal"
