"""Three-tier settings repo.

Public:
  async get(key) -> Any | None
  async set_setting(key, new_value, value_type, rationale, changed_by, authority)
                                                            -> SetResult

Authority enforcement (THE safety logic — see spec amendment §7):
  Tier 1 (halal):  always reject. Audit row written. HalalViolationError raised.
  Tier 2 (safety): reject unless authority == 'operator_safety'.
                   Audit row written. OperatorOnlyKeyError raised on rejection.
  Tier 3:          accepted with any valid authority.

Audit + mutation happen in the same DB transaction. Discord notification
fires on every successful change (and on rejected halal/operator-only
attempts via the policy_changes audit row, surfaced separately).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.settings import _halal, _safety_seed
from trading_sandwich.settings.keys import TIER1_HALAL_KEYS, TIER2_SAFETY_KEYS, tier_of


# --- exceptions / types -----------------------------------------------------


class OperatorOnlyKeyError(Exception):
    """Raised when a Tier 2 key is mutated without operator_safety authority."""


class TypeMismatchError(ValueError):
    """Raised when new_value's type doesn't match value_type."""


_VALID_AUTHORITIES: frozenset[str] = frozenset(
    {"mcp_default", "operator_safety", "seed", "system"}
)
_VALID_CHANGED_BY: frozenset[str] = frozenset(
    {"claude", "operator", "seed", "system"}
)
_VALID_VALUE_TYPES: frozenset[str] = frozenset(
    {"int", "float", "string", "bool", "array", "object"}
)


Authority = Literal["mcp_default", "operator_safety", "seed", "system"]
ChangedBy = Literal["claude", "operator", "seed", "system"]


@dataclass(frozen=True)
class SetResult:
    applied: bool
    old_value: Any | None
    new_value: Any
    rejection_reason: str | None = None


_FALLBACK_POLICY_PATH = Path("policy.yaml")


@lru_cache(maxsize=1)
def _load_policy_yaml() -> dict[str, Any]:
    with open(_FALLBACK_POLICY_PATH) as f:
        return yaml.safe_load(f) or {}


def _cache_clear() -> None:
    _load_policy_yaml.cache_clear()


def _yaml_get(key: str) -> Any:
    """Walk dotted-path through policy.yaml. Returns None if missing."""
    cur: Any = _load_policy_yaml()
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


async def _read_db_row(key: str) -> Any | None:
    """Return the JSONB value for a key, or None if no row.

    Engine is created per-call to avoid event-loop coupling in tests where
    the testcontainer URL changes. In production this layer goes through
    the shared db.engine.get_engine() but during testcontainer-based tests
    each container has its own URL injected via env."""
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text("SELECT value FROM policy_settings WHERE key = :k"),
                {"k": key},
            )
            row = r.first()
            return row[0] if row is not None else None
    finally:
        await engine.dispose()


async def get(key: str) -> Any:
    if key in TIER1_HALAL_KEYS:
        return _halal.read(key)

    if key in TIER2_SAFETY_KEYS:
        db_val = await _read_db_row(key)
        if db_val is not None:
            return db_val
        return _safety_seed.read(key)

    # Tier 3
    db_val = await _read_db_row(key)
    if db_val is not None:
        return db_val
    return _yaml_get(key)


# --- set path ---------------------------------------------------------------


def _typecheck(value: Any, value_type: str) -> None:
    """Validate value matches value_type. Raises TypeMismatchError if not."""
    py_types = {
        "int": int,
        "float": (int, float),  # int is acceptable where float is declared
        "string": str,
        "bool": bool,
        "array": list,
        "object": dict,
    }
    expected = py_types[value_type]
    if value_type == "int" and isinstance(value, bool):
        # bool is an int subclass in Python; reject so True doesn't pass int check
        raise TypeMismatchError(
            f"value_type='int' but got bool {value!r}; use 'bool' instead"
        )
    if not isinstance(value, expected):
        raise TypeMismatchError(
            f"value_type={value_type!r} expects {expected}, got {type(value).__name__} {value!r}"
        )


async def _write_audit_row(
    conn,
    *,
    key: str,
    old_value: Any | None,
    new_value: Any,
    rationale: str,
    changed_by: str,
    authority: str,
    applied: bool,
    rejection_reason: str | None,
    prompt_version: str | None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO policy_changes "
            "(key, old_value, new_value, rationale, changed_by, authority, "
            "applied, rejection_reason, prompt_version) "
            "VALUES (:k, CAST(:ov AS jsonb), CAST(:nv AS jsonb), :r, :cb, :a, "
            ":ap, :rr, :pv)"
        ),
        {
            "k": key,
            "ov": json.dumps(old_value) if old_value is not None else None,
            "nv": json.dumps(new_value),
            "r": rationale,
            "cb": changed_by,
            "a": authority,
            "ap": applied,
            "rr": rejection_reason,
            "pv": prompt_version,
        },
    )


async def set_setting(
    *,
    key: str,
    new_value: Any,
    value_type: str,
    rationale: str,
    changed_by: ChangedBy,
    authority: Authority,
    prompt_version: str | None = None,
) -> SetResult:
    """Three-tier-aware setter with audit row + Discord notification.

    Notification is fired by the caller (MCP tool / Discord listener) after a
    successful return. Repo's job is to enforce authority + persist atomically.

    On rejection (halal or operator-only), still writes a `policy_changes`
    audit row with applied=false before raising.
    """
    if authority not in _VALID_AUTHORITIES:
        raise ValueError(
            f"unknown authority {authority!r}; must be one of {sorted(_VALID_AUTHORITIES)}"
        )
    if changed_by not in _VALID_CHANGED_BY:
        raise ValueError(
            f"unknown changed_by {changed_by!r}; must be one of {sorted(_VALID_CHANGED_BY)}"
        )
    if value_type not in _VALID_VALUE_TYPES:
        raise ValueError(
            f"unknown value_type {value_type!r}; must be one of {sorted(_VALID_VALUE_TYPES)}"
        )

    tier = tier_of(key)
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        # --- Tier 1: halal — always reject ---
        if tier == 1:
            async with engine.begin() as conn:
                await _write_audit_row(
                    conn, key=key, old_value=None, new_value=new_value,
                    rationale=rationale, changed_by=changed_by, authority=authority,
                    applied=False, rejection_reason="halal_inviolable",
                    prompt_version=prompt_version,
                )
            _halal.refuse_write(key, new_value, reason=f"changed_by={changed_by}")
            # refuse_write always raises; this line is unreachable but keeps
            # the type checker honest.
            raise AssertionError("unreachable")  # pragma: no cover

        # --- Tier 2: safety — only operator_safety authority succeeds ---
        if tier == 2 and authority != "operator_safety":
            async with engine.begin() as conn:
                await _write_audit_row(
                    conn, key=key, old_value=None, new_value=new_value,
                    rationale=rationale, changed_by=changed_by, authority=authority,
                    applied=False, rejection_reason="operator_only_key",
                    prompt_version=prompt_version,
                )
            raise OperatorOnlyKeyError(
                f"key {key!r} is Tier 2 (operator-safety). Authority "
                f"{authority!r} cannot mutate it. Use /safety set from Discord."
            )

        # --- Tier 2 with operator_safety, or Tier 3 with anything: apply ---
        _typecheck(new_value, value_type)

        async with engine.begin() as conn:
            r = await conn.execute(
                text("SELECT value, value_type FROM policy_settings WHERE key = :k"),
                {"k": key},
            )
            row = r.first()
            old_value = row[0] if row is not None else None
            old_type = row[1] if row is not None else None
            if old_type is not None and old_type != value_type:
                raise TypeMismatchError(
                    f"key {key!r} previously stored as {old_type!r}, "
                    f"refusing to change type to {value_type!r}. "
                    f"Type changes require a deliberate migration."
                )

            await conn.execute(
                text(
                    "INSERT INTO policy_settings "
                    "(key, value, value_type, updated_by, updated_at) "
                    "VALUES (:k, CAST(:v AS jsonb), :t, :ub, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "value = EXCLUDED.value, "
                    "value_type = EXCLUDED.value_type, "
                    "updated_by = EXCLUDED.updated_by, "
                    "updated_at = NOW()"
                ),
                {"k": key, "v": json.dumps(new_value), "t": value_type, "ub": changed_by},
            )
            await _write_audit_row(
                conn, key=key, old_value=old_value, new_value=new_value,
                rationale=rationale, changed_by=changed_by, authority=authority,
                applied=True, rejection_reason=None,
                prompt_version=prompt_version,
            )

        return SetResult(applied=True, old_value=old_value, new_value=new_value)
    finally:
        await engine.dispose()
