"""Tier 2 (operator-safety) file fallback reader.

Tier 2 keys live in policy.yaml as the seed/default. The repo's get
path checks the DB first; if no policy_settings row exists, it falls
back to this reader. A `/safety reset <key>` deletes the DB row and
the next read returns to the seed value.

This module ONLY reads Tier 2 keys. Reading a Tier 1 or Tier 3 key
through here raises NotSafetyKeyError — that's a programming error.

There is no write path. Writes go through repo.set_setting() with
authority='operator_safety'. The seed file itself is mutated only by
git commits, like any other file-only config.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from trading_sandwich.settings.keys import TIER2_SAFETY_KEYS


_SAFETY_SEED_PATH = Path("policy.yaml")


class NotSafetyKeyError(KeyError):
    """Raised when _safety_seed.read() is called with a non-Tier-2 key."""


@lru_cache(maxsize=1)
def _load_policy() -> dict[str, Any]:
    with open(_SAFETY_SEED_PATH) as f:
        return yaml.safe_load(f) or {}


def _cache_clear() -> None:
    _load_policy.cache_clear()


def read(key: str) -> Any:
    """Return the file-seed value for a Tier 2 key.

    Tier 2 keys are flat (top-level in policy.yaml). If a future Tier 2
    key is nested, switch this to dotted-path traversal like _halal does.
    """
    if key not in TIER2_SAFETY_KEYS:
        raise NotSafetyKeyError(
            f"{key!r} is not a Tier 2 safety key. Use settings.repo for Tier 3, "
            f"settings._halal for Tier 1."
        )
    pol = _load_policy()
    if key not in pol:
        raise KeyError(
            f"Tier 2 safety key {key!r} missing from policy.yaml seed. "
            f"Every Tier 2 key MUST have a file-seed default."
        )
    return pol[key]


def read_all() -> dict[str, Any]:
    """Return the full Tier 2 seed dict, used by snapshot generation.

    Raises if any Tier 2 key is missing from policy.yaml — that's a
    deployment error.
    """
    return {k: read(k) for k in TIER2_SAFETY_KEYS}
