"""Futures-microstructure features: funding, open interest, L/S ratio, OB imbalance."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd


def compute_funding_24h_mean(funding: pd.DataFrame, at_time: datetime) -> Decimal | None:
    """Arithmetic mean of settled funding rates in (at_time - 24h, at_time]."""
    if funding.empty:
        return None
    window_start = at_time - timedelta(hours=24)
    mask = (funding["settlement_time"] > window_start) & (funding["settlement_time"] <= at_time)
    window = funding.loc[mask, "rate"]
    if window.empty:
        return None
    total = sum(Decimal(str(r)) for r in window)
    return total / Decimal(len(window))


def compute_oi_deltas(oi: pd.DataFrame, at_time: datetime) -> tuple[Decimal | None, Decimal | None]:
    """Return (Δ OI vs 1h ago, Δ OI vs 24h ago) in USD.
    Uses the nearest-at-or-before snapshot at each reference time.
    """
    if oi.empty:
        return None, None
    sorted_oi = oi.sort_values("captured_at")

    def _at_or_before(t: datetime) -> Decimal | None:
        mask = sorted_oi["captured_at"] <= t
        if not mask.any():
            return None
        return Decimal(str(sorted_oi.loc[mask, "open_interest_usd"].iloc[-1]))

    now_val = _at_or_before(at_time)
    if now_val is None:
        return None, None
    prev_1h = _at_or_before(at_time - timedelta(hours=1))
    prev_24h = _at_or_before(at_time - timedelta(hours=24))
    d1h = now_val - prev_1h if prev_1h is not None else None
    d24h = now_val - prev_24h if prev_24h is not None else None
    return d1h, d24h


def compute_ob_imbalance_05pct(snapshot: dict, mid_price: Decimal) -> Decimal:
    """Fraction of bid+ask depth that sits on the bid side within ±0.5% of mid.
    Input snapshot shape: {"bids": [[price, size], ...], "asks": [...]}.
    Returns 0.5 (neutral) when the band is empty on both sides.
    """
    band_lower = mid_price * Decimal("0.995")
    band_upper = mid_price * Decimal("1.005")

    bid_depth = Decimal("0")
    for price_s, size_s in snapshot["bids"]:
        price = Decimal(str(price_s))
        if band_lower <= price <= mid_price:
            bid_depth += Decimal(str(size_s))

    ask_depth = Decimal("0")
    for price_s, size_s in snapshot["asks"]:
        price = Decimal(str(price_s))
        if mid_price < price <= band_upper:
            ask_depth += Decimal(str(size_s))

    total = bid_depth + ask_depth
    if total == 0:
        return Decimal("0.5")
    return bid_depth / total
