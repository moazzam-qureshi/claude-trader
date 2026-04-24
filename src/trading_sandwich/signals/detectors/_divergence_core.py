"""Shared divergence-pair finder for divergence_rsi + divergence_macd."""
from __future__ import annotations

from trading_sandwich.contracts.models import FeaturesRow

MIN_PIVOT_SPACING = 5


def find_divergence_pair(
    window: list[FeaturesRow],
    *,
    oscillator_attr: str,
    kind: str,                  # "low" (bullish) or "high" (bearish)
) -> dict | None:
    prices = [(i, float(r.close_price)) for i, r in enumerate(window)]
    osc = [(i, float(getattr(r, oscillator_attr)))
           for i, r in enumerate(window)
           if getattr(r, oscillator_attr) is not None]
    if len(osc) < 2:
        return None

    later_price_lower = (kind == "low")
    later_osc_higher = (kind == "low")

    sorted_pts = (
        sorted(prices, key=lambda t: t[1]) if kind == "low"
        else sorted(prices, key=lambda t: -t[1])
    )

    for i, (idx_a, _pa) in enumerate(sorted_pts):
        for idx_b, _pb in sorted_pts[i + 1:]:
            earlier, later = sorted([idx_a, idx_b])
            if later - earlier < MIN_PIVOT_SPACING:
                continue
            p_earlier = float(window[earlier].close_price)
            p_later = float(window[later].close_price)
            r_earlier = getattr(window[earlier], oscillator_attr)
            r_later = getattr(window[later], oscillator_attr)
            if r_earlier is None or r_later is None:
                continue
            r_earlier = float(r_earlier)
            r_later = float(r_later)
            if later_price_lower and p_later >= p_earlier:
                continue
            if not later_price_lower and p_later <= p_earlier:
                continue
            if later_osc_higher and r_later <= r_earlier:
                continue
            if not later_osc_higher and r_later >= r_earlier:
                continue
            if later < len(window) - 3:
                continue
            return {
                "earlier": earlier, "later": later,
                "p_earlier": p_earlier, "p_later": p_later,
                "osc_earlier": r_earlier, "osc_later": r_later,
            }
    return None
