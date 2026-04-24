"""Central `policy.yaml` accessor. Consumers should use these helpers rather than
reading the YAML directly; this gives us one place to change caching or schema
validation when the policy grows.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

_POLICY_PATH = Path("policy.yaml")


@lru_cache(maxsize=1)
def load_policy() -> dict:
    with open(_POLICY_PATH) as f:
        return yaml.safe_load(f)


def get_confidence_threshold(archetype: str) -> Decimal:
    return Decimal(str(load_policy()["per_archetype_confidence_threshold"][archetype]))


def get_cooldown_minutes(archetype: str) -> int:
    return int(load_policy()["per_archetype_cooldown_minutes"][archetype])


def get_dedup_window_minutes() -> int:
    return int(load_policy()["gating"]["dedup_window_minutes"])


def get_regime_thresholds() -> dict:
    return dict(load_policy()["regime"])


def get_funding_threshold(symbol: str) -> tuple[Decimal, Decimal]:
    table = load_policy()["per_symbol_funding_threshold"]
    entry = table.get(symbol, table["default"])
    return Decimal(str(entry["long"])), Decimal(str(entry["short"]))


def reset_cache() -> None:
    """Test hook — policy.yaml changes mid-process (e.g. in a test) need cache bust."""
    load_policy.cache_clear()
