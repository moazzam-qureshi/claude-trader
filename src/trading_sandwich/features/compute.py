"""Feature orchestrator. Pulls raw-table inputs, runs every indicator module,
applies the regime classifier, returns a dict keyed by `features` table columns.

Phase 0's 3-function API (compute_ema/compute_rsi/compute_atr) is preserved
via re-exports from the indicator package so any remaining Phase 0 callers
keep working without an import change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pandas as pd

from trading_sandwich._policy import get_regime_thresholds
from trading_sandwich.indicators.microstructure import (
    compute_funding_24h_mean,
    compute_ob_imbalance_05pct,
    compute_oi_deltas,
)
from trading_sandwich.indicators.regime_inputs import (
    compute_atr_percentile,
    compute_bb_width_percentile,
    compute_ema_slope_bps,
)
from trading_sandwich.indicators.structure import (
    compute_classic_pivots,
    compute_prior_day_hl,
    compute_prior_week_hl,
    compute_swing_high_low,
)
from trading_sandwich.indicators.trend import (
    compute_adx,
    compute_ema,
    compute_macd,
    compute_roc,
    compute_rsi,
    compute_stoch_rsi,
)
from trading_sandwich.indicators.volatility import (
    compute_atr,
    compute_bollinger,
    compute_donchian,
    compute_keltner,
)
from trading_sandwich.indicators.volume import (
    compute_mfi,
    compute_obv,
    compute_volume_zscore,
    compute_vwap_session,
)
from trading_sandwich.regime.classifier import classify

__all__ = [
    "compute_ema", "compute_rsi", "compute_atr",   # Phase 0 re-exports
    "build_features_row", "RawInputs",
]


@dataclass
class RawInputs:
    """Everything the orchestrator needs at compute time. Assembled by the
    worker before calling `build_features_row`.
    """
    candles: pd.DataFrame
    funding: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["settlement_time", "rate"]))
    open_interest: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["captured_at", "open_interest_usd"]))
    long_short_ratio: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["captured_at", "ratio"]))
    latest_ob_snapshot: dict | None = None


def build_features_row(
    symbol: str, timeframe: str, close_time: datetime,
    inputs: RawInputs,
) -> dict | None:
    """Compute every Phase 1 indicator + regime label for the most-recent
    candle (the one whose close_time matches `close_time`). Returns a dict
    with keys matching the `features` table columns, or None if insufficient
    history.
    """
    df = inputs.candles
    if len(df) < 200:
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    # --- Trend + momentum ---
    ema_8 = compute_ema(close, 8)
    ema_21 = compute_ema(close, 21)
    ema_55 = compute_ema(close, 55)
    ema_200 = compute_ema(close, 200)
    rsi_14 = compute_rsi(close, 14)
    macd_line, macd_signal_s, macd_hist = compute_macd(close)
    adx_14, di_plus_14, di_minus_14 = compute_adx(high, low, close, 14)
    stoch_k, stoch_d = compute_stoch_rsi(close)
    roc_10 = compute_roc(close, 10)

    # --- Volatility + range ---
    atr_14 = compute_atr(high, low, close, 14)
    bb_up, bb_mid, bb_lo, bb_w = compute_bollinger(close, 20, 2.0)
    kc_up, kc_mid, kc_lo = compute_keltner(high, low, close, 20, 2.0)
    dc_up, dc_mid, dc_lo = compute_donchian(high, low, 20)

    # --- Volume + flow ---
    obv = compute_obv(close, volume)
    vwap = compute_vwap_session(df)
    vol_z = compute_volume_zscore(volume, 20)
    mfi_14 = compute_mfi(high, low, close, volume, 14)

    # --- Structure ---
    swing_h, swing_l = compute_swing_high_low(high, low, 5)
    pdh, pdl = compute_prior_day_hl(df)
    pwh, pwl = compute_prior_week_hl(df)
    # Classic pivots for today use yesterday's H/L/close. If prior-day H/L is
    # still warming up, fall back to the previous bar's high/low.
    prev_close_row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    pdh_last = pdh.iloc[-1]
    pdl_last = pdl.iloc[-1]
    p_p, p_r1, p_r2, p_s1, p_s2 = compute_classic_pivots(
        high=float(pdh_last) if pd.notna(pdh_last) else float(prev_close_row["high"]),
        low=float(pdl_last) if pd.notna(pdl_last) else float(prev_close_row["low"]),
        close=float(prev_close_row["close"]),
    )

    # --- Regime inputs ---
    slope_bps = compute_ema_slope_bps(ema_21, window=10)
    atr_pct_100 = compute_atr_percentile(atr_14, window=100)
    bbw_pct_100 = compute_bb_width_percentile(bb_w, window=100)

    # --- Microstructure ---
    fr_24h_mean = compute_funding_24h_mean(inputs.funding, close_time)
    latest_funding_rate = (
        Decimal(str(inputs.funding["rate"].iloc[-1]))
        if not inputs.funding.empty else None
    )
    latest_oi_usd = (
        Decimal(str(inputs.open_interest["open_interest_usd"].iloc[-1]))
        if not inputs.open_interest.empty else None
    )
    d_oi_1h, d_oi_24h = compute_oi_deltas(inputs.open_interest, close_time)
    latest_lsr = (
        Decimal(str(inputs.long_short_ratio["ratio"].iloc[-1]))
        if not inputs.long_short_ratio.empty else None
    )
    ob_imb = None
    if inputs.latest_ob_snapshot is not None and pd.notna(bb_mid.iloc[-1]):
        ob_imb = compute_ob_imbalance_05pct(
            inputs.latest_ob_snapshot, Decimal(str(close.iloc[-1])),
        )

    # --- Regime classification ---
    trend_regime, vol_regime = classify(
        close=Decimal(str(close.iloc[-1])),
        ema_55=_dec_or_none(ema_55.iloc[-1]),
        ema_slope_bps=_float_or_none(slope_bps.iloc[-1]),
        adx=_float_or_none(adx_14.iloc[-1]),
        bb_width_percentile_100=_float_or_none(bbw_pct_100.iloc[-1]),
        policy=get_regime_thresholds(),
    )

    return {
        "symbol": symbol, "timeframe": timeframe, "close_time": close_time,
        "close_price": Decimal(str(close.iloc[-1])),
        "ema_8":   _dec_or_none(ema_8.iloc[-1]),
        "ema_21":  _dec_or_none(ema_21.iloc[-1]),
        "ema_55":  _dec_or_none(ema_55.iloc[-1]),
        "ema_200": _dec_or_none(ema_200.iloc[-1]),
        "rsi_14":  _dec_or_none(rsi_14.iloc[-1]),
        "atr_14":  _dec_or_none(atr_14.iloc[-1]),
        "macd_line":   _dec_or_none(macd_line.iloc[-1]),
        "macd_signal": _dec_or_none(macd_signal_s.iloc[-1]),
        "macd_hist":   _dec_or_none(macd_hist.iloc[-1]),
        "adx_14":      _dec_or_none(adx_14.iloc[-1]),
        "di_plus_14":  _dec_or_none(di_plus_14.iloc[-1]),
        "di_minus_14": _dec_or_none(di_minus_14.iloc[-1]),
        "stoch_rsi_k": _dec_or_none(stoch_k.iloc[-1]),
        "stoch_rsi_d": _dec_or_none(stoch_d.iloc[-1]),
        "roc_10":      _dec_or_none(roc_10.iloc[-1]),
        "bb_upper":    _dec_or_none(bb_up.iloc[-1]),
        "bb_middle":   _dec_or_none(bb_mid.iloc[-1]),
        "bb_lower":    _dec_or_none(bb_lo.iloc[-1]),
        "bb_width":    _dec_or_none(bb_w.iloc[-1]),
        "keltner_upper":  _dec_or_none(kc_up.iloc[-1]),
        "keltner_middle": _dec_or_none(kc_mid.iloc[-1]),
        "keltner_lower":  _dec_or_none(kc_lo.iloc[-1]),
        "donchian_upper":  _dec_or_none(dc_up.iloc[-1]),
        "donchian_middle": _dec_or_none(dc_mid.iloc[-1]),
        "donchian_lower":  _dec_or_none(dc_lo.iloc[-1]),
        "obv":              _dec_or_none(obv.iloc[-1]),
        "vwap":             _dec_or_none(vwap.iloc[-1]),
        "volume_zscore_20": _dec_or_none(vol_z.iloc[-1]),
        "mfi_14":           _dec_or_none(mfi_14.iloc[-1]),
        "swing_high_5":     _dec_or_none(swing_h.iloc[-1]),
        "swing_low_5":      _dec_or_none(swing_l.iloc[-1]),
        "pivot_p":  Decimal(str(p_p)),
        "pivot_r1": Decimal(str(p_r1)),
        "pivot_r2": Decimal(str(p_r2)),
        "pivot_s1": Decimal(str(p_s1)),
        "pivot_s2": Decimal(str(p_s2)),
        "prior_day_high":  _dec_or_none(pdh.iloc[-1]),
        "prior_day_low":   _dec_or_none(pdl.iloc[-1]),
        "prior_week_high": _dec_or_none(pwh.iloc[-1]),
        "prior_week_low":  _dec_or_none(pwl.iloc[-1]),
        "funding_rate":          latest_funding_rate,
        "funding_rate_24h_mean": fr_24h_mean,
        "open_interest_usd": latest_oi_usd,
        "oi_delta_1h":       d_oi_1h,
        "oi_delta_24h":      d_oi_24h,
        "long_short_ratio":  latest_lsr,
        "ob_imbalance_05":   ob_imb,
        "ema_21_slope_bps":         _dec_or_none(slope_bps.iloc[-1]),
        "atr_percentile_100":       _dec_or_none(atr_pct_100.iloc[-1]),
        "bb_width_percentile_100":  _dec_or_none(bbw_pct_100.iloc[-1]),
        "trend_regime": trend_regime,
        "vol_regime":   vol_regime,
    }


def _dec_or_none(x) -> Decimal | None:
    if pd.isna(x):
        return None
    return Decimal(str(float(x)))


def _float_or_none(x) -> float | None:
    if pd.isna(x):
        return None
    return float(x)
