"""Three-tier classification of policy keys.

This module is the single source of truth for which keys belong to which tier.
Every other settings module reads from these sets. Tests in
tests/unit/test_settings_keys_and_halal.py pin the contents — moving a key
between tiers requires updating both the set and the test, which forces a
deliberate decision rather than silent drift.
"""
from __future__ import annotations


TIER1_HALAL_KEYS: frozenset[str] = frozenset({
    "max_leverage",
    "longs_only",
    "universe.tiers.excluded",
    "universe.hard_limits.excluded_symbols_locked",
})


TIER2_SAFETY_KEYS: frozenset[str] = frozenset({
    "max_account_drawdown_pct",
    "max_daily_realized_loss_usd",
    "trading_enabled",
    "auto_flatten_on_kill",
})


def tier_of(key: str) -> int:
    if key in TIER1_HALAL_KEYS:
        return 1
    if key in TIER2_SAFETY_KEYS:
        return 2
    return 3


assert not (TIER1_HALAL_KEYS & TIER2_SAFETY_KEYS), "tier sets must be disjoint"
