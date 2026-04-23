"""Pure outcome-measurement helpers. Input: entry info + forward candles DataFrame.
Output: dict keyed by outcome column names.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd


def measure_forward(
    *,
    entry_price: Decimal,
    direction: str,
    atr: Decimal,
    candles: pd.DataFrame,
) -> dict:
    if candles.empty:
        raise ValueError("measure_forward: candles DataFrame is empty")

    sign = Decimal("1") if direction == "long" else Decimal("-1")

    final_close = Decimal(str(candles["close"].iloc[-1]))
    return_pct = ((final_close - entry_price) / entry_price) * sign

    highs = candles["high"].astype(float)
    lows = candles["low"].astype(float)
    entry_f = float(entry_price)

    zero = Decimal("0")
    if direction == "long":
        raw_mfe = Decimal(str((highs.max() - entry_f) / entry_f))
        raw_mae = Decimal(str((lows.min() - entry_f) / entry_f))
    else:
        raw_mfe = Decimal(str((entry_f - lows.min()) / entry_f))
        raw_mae = Decimal(str((entry_f - highs.max()) / entry_f))
    # MFE (favorable) and MAE (adverse) are clamped so they always report
    # excursion relative to entry, not a "reversed" excursion when price
    # never went the wrong way.
    mfe_pct = raw_mfe if raw_mfe > zero else zero
    mae_pct = raw_mae if raw_mae < zero else zero

    atr_f = float(atr)
    if direction == "long":
        stop_level = entry_f - atr_f
        target_level = entry_f + 2 * atr_f
        stop_hit_series = lows <= stop_level
        target_hit_series = highs >= target_level
    else:
        stop_level = entry_f + atr_f
        target_level = entry_f - 2 * atr_f
        stop_hit_series = highs >= stop_level
        target_hit_series = lows <= target_level

    first_stop_idx = stop_hit_series.idxmax() if stop_hit_series.any() else None
    first_target_idx = target_hit_series.idxmax() if target_hit_series.any() else None

    def _time_seconds(idx) -> int | None:
        if idx is None:
            return None
        entry_t = candles["close_time"].iloc[0]
        hit_t = candles["close_time"].iloc[idx]
        return int((hit_t - entry_t).total_seconds())

    return {
        "close_price": final_close,
        "return_pct": return_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "mfe_in_atr": mfe_pct / (atr / entry_price) if atr else None,
        "mae_in_atr": mae_pct / (atr / entry_price) if atr else None,
        "stop_hit_1atr": bool(first_stop_idx is not None),
        "target_hit_2atr": bool(first_target_idx is not None),
        "time_to_stop_s": _time_seconds(first_stop_idx),
        "time_to_target_s": _time_seconds(first_target_idx),
    }
