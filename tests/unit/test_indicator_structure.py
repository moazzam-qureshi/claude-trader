from datetime import UTC, datetime, timedelta

import pandas as pd

from trading_sandwich.indicators.structure import (
    compute_classic_pivots,
    compute_prior_day_hl,
    compute_prior_week_hl,
    compute_swing_high_low,
)


def test_swing_high_is_5_bar_fractal_peak():
    # Peak at bar 5, trough at bar 11; both should be unique in their 5-bar window.
    highs = pd.Series([1.0, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1, 0.2, 1, 2, 3])
    lows  = pd.Series([0.9, 1, 2, 3, 4, 9,  4, 3, 2, 1, 0.5, 0.1, 0.5, 1, 2])
    sh, sl = compute_swing_high_low(highs, lows, lookback=5)
    assert float(sh.iloc[10]) == 10.0   # high peak carried forward
    # Swing low confirmed at index 11 once the right-side of its 5-bar window exists
    assert float(sl.iloc[13]) == 0.1


def test_classic_pivots_arithmetic():
    # For H=110, L=90, C=100: P=100, R1=110, S1=90, R2=120, S2=80
    p, r1, r2, s1, s2 = compute_classic_pivots(high=110, low=90, close=100)
    assert (p, r1, r2, s1, s2) == (100.0, 110.0, 120.0, 90.0, 80.0)


def test_prior_day_high_low():
    base = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    rows = []
    for day in range(3):
        for hour in range(24):
            ct = base + timedelta(days=day, hours=hour)
            close = 100 + day * 10 + hour * 0.1
            rows.append({
                "close_time": ct,
                "high": close + 0.5, "low": close - 0.5,
            })
    df = pd.DataFrame(rows)
    pdh, pdl = compute_prior_day_hl(df)
    day0_high = max(100 + h * 0.1 + 0.5 for h in range(24))
    day0_low = min(100 + h * 0.1 - 0.5 for h in range(24))
    assert abs(float(pdh.iloc[24]) - day0_high) < 1e-9
    assert abs(float(pdl.iloc[24]) - day0_low) < 1e-9


def test_prior_week_hl_needs_full_prior_week():
    base = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)  # Monday
    rows = []
    for day in range(14):
        ct = base + timedelta(days=day, hours=12)
        rows.append({
            "close_time": ct,
            "high": 100 + day, "low": 100 - day,
        })
    df = pd.DataFrame(rows)
    pwh, pwl = compute_prior_week_hl(df)
    assert pwh.iloc[7] == 106.0
    assert pwl.iloc[7] == 94.0
