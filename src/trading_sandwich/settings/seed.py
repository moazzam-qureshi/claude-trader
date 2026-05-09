"""First-boot bootstrap of `policy_settings` from `policy.yaml`.

Walks every leaf in policy.yaml and upserts a row in `policy_settings`
keyed by the dotted path. Excludes Tier 1 (halal) keys outright — those
are file-only and have no DB representation. Tier 2 keys are seeded with
`changed_by='seed', authority='seed'`; runtime overrides go through
`/safety set`. Tier 3 keys are seeded the same way; runtime tunes go
through the standard repo path.

Idempotency: running bootstrap twice on a populated table is a no-op.
Existing rows are NEVER overwritten by bootstrap unless explicitly
listed in `force_reseed_keys` (the `cli settings reseed --key K` path).

This module bypasses `repo.set_setting()` deliberately: that path enforces
authority rules that would reject `authority='seed'` on Tier 2 keys, but
seeding IS the legitimate first-boot path for those values. The repo
authority gate exists to stop runtime mutation through the wrong
codepath; bootstrap is a separate, narrow, operator-initiated codepath.
The 9 safety-critical tests on `repo.set_setting()` remain authoritative
for runtime mutation.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §12.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.settings._halal import HalalViolationError
from trading_sandwich.settings.keys import TIER1_HALAL_KEYS


_SEED_PATH = Path("policy.yaml")
_SEED_RATIONALE = "initial bootstrap from policy.yaml"


class NoYamlDefaultError(KeyError):
    """Raised when force_reseed_keys names a key absent from policy.yaml."""


@dataclass
class BootstrapReport:
    inserted_count: int = 0
    skipped_count: int = 0
    reseeded_count: int = 0
    inserted_keys: list[str] = field(default_factory=list)
    skipped_keys: list[str] = field(default_factory=list)
    reseeded_keys: list[str] = field(default_factory=list)


def _infer_value_type(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    raise TypeError(f"unsupported yaml leaf type: {type(v).__name__} -> {v!r}")


def _is_under_tier1(dotted: str) -> bool:
    """Check whether a dotted path is itself or sits beneath a Tier 1 key.

    e.g. 'universe.tiers.excluded.symbols' is locked under
    'universe.tiers.excluded'.
    """
    if dotted in TIER1_HALAL_KEYS:
        return True
    for halal in TIER1_HALAL_KEYS:
        if dotted.startswith(halal + "."):
            return True
    return False


def _walk_leaves(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield (dotted_key, value) for every leaf in a yaml-loaded structure.

    A leaf is anything that is not a dict. Lists are leaves (stored as
    `array` JSONB). Empty dicts are skipped silently.
    """
    if isinstance(node, dict):
        if not node:
            return
        for k, v in node.items():
            child_key = f"{prefix}.{k}" if prefix else str(k)
            yield from _walk_leaves(v, child_key)
    else:
        yield prefix, node


@lru_cache(maxsize=1)
def _load_yaml() -> dict[str, Any]:
    with open(_SEED_PATH) as f:
        return yaml.safe_load(f) or {}


def _cache_clear() -> None:
    _load_yaml.cache_clear()


def _yaml_lookup(dotted: str) -> Any:
    """Return the leaf value for a dotted path. Raises KeyError if missing."""
    cur: Any = _load_yaml()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    if isinstance(cur, dict):
        raise KeyError(f"{dotted}: not a leaf (still a mapping)")
    return cur


async def _existing_keys(conn) -> set[str]:
    r = await conn.execute(text("SELECT key FROM policy_settings"))
    return {row[0] for row in r}


async def _insert_row(
    conn, *, key: str, value: Any, value_type: str, audit: bool = True
) -> None:
    await conn.execute(
        text(
            "INSERT INTO policy_settings "
            "(key, value, value_type, updated_by, updated_at) "
            "VALUES (:k, CAST(:v AS jsonb), :t, 'seed', NOW()) "
            "ON CONFLICT (key) DO UPDATE SET "
            "value = EXCLUDED.value, "
            "value_type = EXCLUDED.value_type, "
            "updated_by = 'seed', "
            "updated_at = NOW()"
        ),
        {"k": key, "v": json.dumps(value), "t": value_type},
    )
    if audit:
        await conn.execute(
            text(
                "INSERT INTO policy_changes "
                "(key, old_value, new_value, rationale, changed_by, "
                "authority, applied, rejection_reason, prompt_version) "
                "VALUES (:k, NULL, CAST(:nv AS jsonb), :r, 'seed', 'seed', "
                "true, NULL, NULL)"
            ),
            {"k": key, "nv": json.dumps(value), "r": _SEED_RATIONALE},
        )


async def bootstrap(
    *, force_reseed_keys: list[str] | None = None
) -> BootstrapReport:
    """Bootstrap policy_settings from policy.yaml.

    Behavior:
      - For every non-Tier-1 leaf in policy.yaml: insert if absent from
        policy_settings; skip if present.
      - For each key in `force_reseed_keys`: overwrite to YAML default
        (raises NoYamlDefaultError if not in YAML; HalalViolationError
        if it's a Tier 1 key).

    Both branches log a `policy_changes` audit row with `changed_by='seed'`,
    `authority='seed'`, `applied=true`, rationale `_SEED_RATIONALE`.
    """
    _cache_clear()
    pol = _load_yaml()
    report = BootstrapReport()

    force_keys = list(force_reseed_keys or [])
    # Validate force_reseed_keys up front so a bad call doesn't half-apply
    for fk in force_keys:
        if _is_under_tier1(fk):
            raise HalalViolationError(
                f"refusing to reseed Tier 1 halal key {fk!r}; "
                f"halal values are inviolable and never DB-backed."
            )
        try:
            _yaml_lookup(fk)
        except KeyError as e:
            raise NoYamlDefaultError(f"{fk!r} not_in_yaml") from e

    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            existing = await _existing_keys(conn)

            # Insert missing leaves
            for dotted, value in _walk_leaves(pol):
                if _is_under_tier1(dotted):
                    continue
                if dotted in existing:
                    report.skipped_count += 1
                    report.skipped_keys.append(dotted)
                    continue
                vtype = _infer_value_type(value)
                await _insert_row(conn, key=dotted, value=value, value_type=vtype)
                report.inserted_count += 1
                report.inserted_keys.append(dotted)

            # Force-reseed named keys
            for fk in force_keys:
                value = _yaml_lookup(fk)
                vtype = _infer_value_type(value)
                await _insert_row(conn, key=fk, value=value, value_type=vtype)
                report.reseeded_count += 1
                report.reseeded_keys.append(fk)
    finally:
        await engine.dispose()

    return report
