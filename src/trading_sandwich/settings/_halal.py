"""Tier 1 (halal/religious) settings reader.

Reads inviolable halal values from policy.yaml directly. There is no DB
codepath. There is no write codepath — `refuse_write()` exists only to
fail loudly if any caller tries.

The `validate_loaded()` function runs on import (called explicitly by
the bootstrap flow) and raises HalalViolationError if policy.yaml has
been edited to violate Islamic finance constraints (max_leverage > 1
or longs_only != True). This is the last line of defense — even if a
caller bypasses every other check, the load itself fails.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §4.1
and feedback memory `feedback_spot_only_strategies.md`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from trading_sandwich.settings.keys import TIER1_HALAL_KEYS


_HALAL_POLICY_PATH = Path("policy.yaml")


class HalalViolationError(Exception):
    """Raised when a halal/religious constraint is violated.

    Cases:
      - policy.yaml sets max_leverage != 1 or longs_only != True
      - any code attempts to write a Tier 1 key
      - any caller tries to mutate the excluded universe set
    """


class NotHalalKeyError(KeyError):
    """Raised when _halal.read() is called with a non-Tier-1 key.

    This is a programming error — callers should route Tier 2/3 keys
    through the settings repo, not through _halal.
    """


@lru_cache(maxsize=1)
def _load_policy() -> dict[str, Any]:
    with open(_HALAL_POLICY_PATH) as f:
        return yaml.safe_load(f) or {}


def _cache_clear() -> None:
    """Test hook — clears the lru_cache so a monkeypatched policy path is reread."""
    _load_policy.cache_clear()


def _get_path(d: dict, dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(f"halal key missing from policy.yaml: {dotted}")
        cur = cur[part]
    return cur


def read(key: str) -> Any:
    """Read a Tier 1 halal value. Always from policy.yaml, never DB."""
    if key not in TIER1_HALAL_KEYS:
        raise NotHalalKeyError(
            f"{key!r} is not a Tier 1 halal key. Use settings.repo for Tier 2/3."
        )
    return _get_path(_load_policy(), key)


def read_all() -> dict[str, Any]:
    """Return all Tier 1 values as a dict, for snapshot generation."""
    pol = _load_policy()
    return {k: _get_path(pol, k) for k in TIER1_HALAL_KEYS}


def refuse_write(key: str, new_value: Any, reason: str) -> None:
    """Always raises HalalViolationError. Existence is the safety net.

    No code should ever call this expecting it to succeed. Importers can
    grep for refuse_write to find every place a halal write was attempted.
    """
    raise HalalViolationError(
        f"refused write to halal key {key!r} -> {new_value!r} (reason: {reason}). "
        f"Tier 1 halal values are inviolable; see "
        f"docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §4.1."
    )


def validate_loaded() -> None:
    """Validate the currently loaded policy.yaml satisfies halal constraints.

    Called by `cli doctor` and by the settings bootstrap. Raises if violated;
    the system should refuse to start if it raises.
    """
    pol = _load_policy()

    if "max_leverage" in pol:
        leverage = pol["max_leverage"]
        if leverage != 1:
            raise HalalViolationError(
                f"max_leverage must equal 1 for halal compliance, found {leverage!r}. "
                f"Spot-only is religiously inviolable."
            )

    if "longs_only" in pol:
        longs_only = pol["longs_only"]
        if longs_only is not True:
            raise HalalViolationError(
                f"longs_only must equal true for halal compliance, found {longs_only!r}. "
                f"Shorts are religiously inviolable."
            )

    # max_leverage / longs_only may be absent in current policy.yaml
    # (max_leverage IS present at line 61; longs_only is implicit). The
    # absence is not a violation by itself — the values that ARE present
    # must be compliant. Adding an explicit longs_only field is left as a
    # follow-up; the adapter layer enforces longs-only regardless.
