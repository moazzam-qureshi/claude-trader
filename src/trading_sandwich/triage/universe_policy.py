"""Universe policy: load policy.yaml universe section, validate against hard
limits, apply mutations atomically."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)


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
        return sum(len(self.tiers[t]) for t in ("core", "watchlist", "observation"))

    def tier_of(self, symbol: str) -> str | None:
        for t in ("core", "watchlist", "observation", "excluded"):
            if symbol in self.tiers.get(t, []):
                return t
        return None


def load_universe(policy_path: Path) -> UniversePolicy:
    raw = yaml.safe_load(policy_path.read_text())
    universe = raw["universe"]
    tiers = {
        t: list(universe["tiers"].get(t, {}).get("symbols", []))
        for t in ("core", "watchlist", "observation", "excluded")
    }
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

    if req.to_tier and req.to_tier in ("core", "watchlist", "observation"):
        cap = hl.get("max_per_tier", {}).get(req.to_tier)
        if cap is not None and len(policy.tiers[req.to_tier]) >= cap:
            raise HardLimitViolation(
                "max_per_tier",
                f"{req.to_tier} tier at cap {cap}",
            )


def apply_mutation(
    policy_path: Path,
    policy: UniversePolicy,
    req: UniverseMutationRequest,
) -> None:
    raw = policy.raw
    tiers_section = raw["universe"]["tiers"]

    def _remove_from_all(symbol: str) -> str | None:
        for t in ("core", "watchlist", "observation", "excluded"):
            symbols = tiers_section.get(t, {}).get("symbols", [])
            if symbol in symbols:
                symbols.remove(symbol)
                return t
        return None

    def _add_to(tier: str, symbol: str) -> None:
        tiers_section[tier]["symbols"].append(symbol)

    et = req.event_type
    if et == UniverseEventType.ADD:
        _add_to(req.to_tier, req.symbol)
    elif et == UniverseEventType.PROMOTE or et == UniverseEventType.DEMOTE:
        _remove_from_all(req.symbol)
        _add_to(req.to_tier, req.symbol)
    elif et == UniverseEventType.REMOVE:
        _remove_from_all(req.symbol)
    elif et == UniverseEventType.EXCLUDE:
        _remove_from_all(req.symbol)
        _add_to("excluded", req.symbol)
    elif et == UniverseEventType.UNEXCLUDE:
        _remove_from_all(req.symbol)
        _add_to(req.to_tier, req.symbol)

    serialized = yaml.safe_dump(raw, sort_keys=False)
    tmp = policy_path.with_suffix(policy_path.suffix + ".tmp")
    tmp.write_text(serialized)
    os.replace(tmp, policy_path)
