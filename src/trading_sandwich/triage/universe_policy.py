"""Universe policy: load policy.yaml universe section, validate against hard
limits, apply mutations atomically.

Phase 3 (spec §6.1) renamed tier `watchlist` to `active`, expanded the
roster to the full halal candidate set, and sub-categorized `excluded`
into `symbols_lending` / `symbols_perp_protocols` / `symbols_memecoins`.
The loader flattens the sub-categorized excluded block into a single
list at `policy.tiers['excluded']` for membership checks; the
sub-categorization survives on disk so the REASON for exclusion stays
queryable. EXCLUDE mutations route to `symbols_memecoins` by default
since structural-haram exclusions (lending, perps) are caught at the
universe-add layer, not via runtime EXCLUDE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)


_ACTIVE_TIERS = ("core", "active", "observation")
_ALL_TIERS = (*_ACTIVE_TIERS, "excluded")
_EXCLUDED_SUBKEYS = ("symbols_lending", "symbols_perp_protocols", "symbols_memecoins")
_EXCLUDED_DEFAULT_BUCKET = "symbols_memecoins"


class HardLimitViolation(Exception):
    def __init__(self, limit: str, message: str):
        self.limit = limit
        super().__init__(f"{limit}: {message}")


@dataclass
class UniversePolicy:
    raw: dict
    tiers: dict[str, list[str]]
    hard_limits: dict

    @property
    def total_size(self) -> int:
        return sum(len(self.tiers[t]) for t in _ACTIVE_TIERS)

    def tier_of(self, symbol: str) -> str | None:
        for t in _ALL_TIERS:
            if symbol in self.tiers.get(t, []):
                return t
        return None


def _flatten_excluded(excluded_block: dict) -> list[str]:
    """Flatten the sub-categorized excluded block into one list. The
    block has three keyed sublists by exclusion reason; for membership
    checks we want the union."""
    out: list[str] = []
    for subkey in _EXCLUDED_SUBKEYS:
        out.extend(excluded_block.get(subkey, []))
    return out


def load_universe(policy_path: Path) -> UniversePolicy:
    raw = yaml.safe_load(policy_path.read_text())
    universe = raw["universe"]
    tiers_block = universe["tiers"]
    tiers = {t: list(tiers_block.get(t, {}).get("symbols", [])) for t in _ACTIVE_TIERS}
    tiers["excluded"] = _flatten_excluded(tiers_block.get("excluded", {}))
    return UniversePolicy(raw=raw, tiers=tiers, hard_limits=universe["hard_limits"])


def validate_mutation(policy: UniversePolicy, req: UniverseMutationRequest) -> None:
    hl = policy.hard_limits

    if req.event_type == UniverseEventType.UNEXCLUDE:
        if req.symbol in hl.get("excluded_symbols_locked", []):
            raise HardLimitViolation(
                "excluded_symbols_locked",
                f"{req.symbol} is operator-locked",
            )

    if req.event_type == UniverseEventType.PROMOTE and req.to_tier == "core":
        if hl.get("core_promotions_operator_only"):
            raise HardLimitViolation(
                "core_promotions_operator_only",
                "core promotions are operator-only",
            )

    if req.event_type in (UniverseEventType.ADD, UniverseEventType.UNEXCLUDE):
        if policy.total_size >= hl.get("max_total_universe_size", 1_000_000):
            raise HardLimitViolation(
                "max_total_universe_size",
                "universe is at maximum size",
            )

    if req.to_tier and req.to_tier in _ACTIVE_TIERS:
        cap = hl.get("max_per_tier", {}).get(req.to_tier)
        if cap is not None and len(policy.tiers[req.to_tier]) >= cap:
            raise HardLimitViolation(
                "max_per_tier",
                f"{req.to_tier} tier at cap {cap}",
            )


def _excluded_block(tiers_section: dict) -> dict:
    block = tiers_section.setdefault("excluded", {})
    for subkey in _EXCLUDED_SUBKEYS:
        block.setdefault(subkey, [])
    return block


def _excluded_all_symbols(tiers_section: dict) -> list[str]:
    return _flatten_excluded(tiers_section.get("excluded", {}))


def apply_mutation(
    policy_path: Path,
    policy: UniversePolicy,
    req: UniverseMutationRequest,
) -> None:
    raw = policy.raw
    tiers_section = raw["universe"]["tiers"]

    def _remove_from_all(symbol: str) -> str | None:
        """Remove `symbol` from whichever tier holds it. Returns the tier
        name (or None if not found). For excluded, scans all three
        sub-buckets."""
        for t in _ACTIVE_TIERS:
            symbols = tiers_section.get(t, {}).get("symbols", [])
            if symbol in symbols:
                symbols.remove(symbol)
                return t
        excluded = _excluded_block(tiers_section)
        for subkey in _EXCLUDED_SUBKEYS:
            bucket = excluded[subkey]
            if symbol in bucket:
                bucket.remove(symbol)
                return "excluded"
        return None

    def _add_to_active_tier(tier: str, symbol: str) -> None:
        tiers_section[tier]["symbols"].append(symbol)

    def _add_to_excluded(symbol: str, subcategory: str = _EXCLUDED_DEFAULT_BUCKET) -> None:
        block = _excluded_block(tiers_section)
        if subcategory not in _EXCLUDED_SUBKEYS:
            subcategory = _EXCLUDED_DEFAULT_BUCKET
        block[subcategory].append(symbol)

    et = req.event_type
    if et == UniverseEventType.ADD:
        _add_to_active_tier(req.to_tier, req.symbol)
    elif et == UniverseEventType.PROMOTE or et == UniverseEventType.DEMOTE:
        _remove_from_all(req.symbol)
        _add_to_active_tier(req.to_tier, req.symbol)
    elif et == UniverseEventType.REMOVE:
        _remove_from_all(req.symbol)
    elif et == UniverseEventType.EXCLUDE:
        _remove_from_all(req.symbol)
        _add_to_excluded(req.symbol)
    elif et == UniverseEventType.UNEXCLUDE:
        _remove_from_all(req.symbol)
        _add_to_active_tier(req.to_tier, req.symbol)

    serialized = yaml.safe_dump(raw, sort_keys=False)
    tmp = policy_path.with_suffix(policy_path.suffix + ".tmp")
    tmp.write_text(serialized)
    os.replace(tmp, policy_path)
