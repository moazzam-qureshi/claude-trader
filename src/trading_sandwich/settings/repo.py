"""Three-tier settings repo (get path).

Public:
  async get(key) -> Any | None
    Tier 1 (halal): _halal.read (file only).
    Tier 2 (safety): DB row if present, else _safety_seed.read (file fallback).
    Tier 3: DB row if present, else policy.yaml default; None if not even the yaml has it.

The set path lands in AM-4d.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.settings import _halal, _safety_seed
from trading_sandwich.settings.keys import TIER1_HALAL_KEYS, TIER2_SAFETY_KEYS


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
