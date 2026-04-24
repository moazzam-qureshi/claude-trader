from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from trading_sandwich.indicators.microstructure import (
    compute_funding_24h_mean,
    compute_ob_imbalance_05pct,
    compute_oi_deltas,
)


def test_funding_24h_mean_three_settlements():
    base = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)
    funding = pd.DataFrame([
        {"settlement_time": base,                   "rate": Decimal("0.0001")},
        {"settlement_time": base + timedelta(hours=8),  "rate": Decimal("0.0002")},
        {"settlement_time": base + timedelta(hours=16), "rate": Decimal("0.0003")},
    ])
    # Window is (at_time - 24h, at_time]. Query at base+16h+epsilon to include
    # all three settlements in-window.
    mean = compute_funding_24h_mean(funding, at_time=base + timedelta(hours=16, minutes=1))
    assert abs(float(mean) - 0.0002) < 1e-9


def test_funding_24h_mean_empty_returns_none():
    funding = pd.DataFrame(columns=["settlement_time", "rate"])
    assert compute_funding_24h_mean(funding, at_time=datetime.now(UTC)) is None


def test_oi_deltas_basic():
    base = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)
    oi = pd.DataFrame([
        {"captured_at": base - timedelta(hours=24, minutes=5), "open_interest_usd": Decimal("1_000_000_000")},
        {"captured_at": base - timedelta(hours=24),             "open_interest_usd": Decimal("1_000_000_000")},
        {"captured_at": base - timedelta(hours=1),              "open_interest_usd": Decimal("1_050_000_000")},
        {"captured_at": base,                                   "open_interest_usd": Decimal("1_100_000_000")},
    ])
    d1h, d24h = compute_oi_deltas(oi, at_time=base)
    assert abs(float(d1h) - 50_000_000) < 1e-6
    assert abs(float(d24h) - 100_000_000) < 1e-6


def test_ob_imbalance_at_0_5pct():
    snap = {
        "bids": [["99.8", "10"], ["99.2", "5"]],
        "asks": [["100.3", "7"], ["100.9", "4"]],
    }
    v = compute_ob_imbalance_05pct(snap, mid_price=Decimal("100"))
    assert abs(float(v) - 10.0 / 17.0) < 1e-6


def test_ob_imbalance_empty_band_returns_half():
    snap = {
        "bids": [["90", "1"]],
        "asks": [["110", "1"]],
    }
    v = compute_ob_imbalance_05pct(snap, mid_price=Decimal("100"))
    assert float(v) == 0.5
