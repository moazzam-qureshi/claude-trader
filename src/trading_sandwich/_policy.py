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


def is_trading_enabled() -> bool:
    return bool(load_policy().get("trading_enabled", False))


def get_execution_mode() -> str:
    mode = load_policy().get("execution_mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError(f"invalid execution_mode: {mode}")
    return mode


def get_proposal_ttl_minutes() -> int:
    return int(load_policy().get("proposal_ttl_minutes", 15))


def get_first_trade_size_multiplier() -> Decimal:
    return Decimal(str(load_policy().get("first_trade_size_multiplier", 0.5)))


def get_claude_daily_triage_cap() -> int:
    return int(load_policy().get("claude_daily_triage_cap", 20))


def get_min_minutes_between_triages() -> int:
    """Global rate limit between any two claude_triaged signals.
    Protects Claude Max session quota when multiple archetypes fire close
    together. 0 disables the gate (fall back to per-archetype cooldowns)."""
    return int(load_policy().get("min_minutes_between_triages", 0))


def get_paper_starting_equity_usd() -> Decimal:
    return Decimal(str(load_policy().get("paper_starting_equity_usd", 10000)))


def get_auto_flatten_on_kill() -> bool:
    return bool(load_policy().get("auto_flatten_on_kill", False))


def get_reconciliation_block_tolerance() -> dict:
    return dict(load_policy().get("reconciliation_block_tolerance", {
        "position_base_drift_pct": 0.5,
        "open_order_count_drift": 0,
    }))


def get_max_order_usd() -> Decimal:
    return Decimal(str(load_policy()["max_order_usd"]))


def get_default_rr_minimum() -> Decimal:
    return Decimal(str(load_policy()["default_rr_minimum"]))


def get_min_stop_distance_atr() -> Decimal:
    return Decimal(str(load_policy()["min_stop_distance_atr"]))


def get_max_stop_distance_atr() -> Decimal:
    return Decimal(str(load_policy()["max_stop_distance_atr"]))


def get_universe_symbols() -> list[str]:
    return list(load_policy()["universe"])
