"""MCP tools for the three-tier settings repo.

Surface (per spec amendment §8):
  get_setting(key)                          -> dict (value + metadata)
  list_settings(prefix="")                  -> list[dict]
  set_setting(key, value, rationale)        -> dict (applied | error)
  get_setting_history(key, limit=20)        -> list[dict]

set_setting always passes authority='mcp_default', changed_by='claude'.
Tier 1 (halal) and Tier 2 (operator-safety) writes are caught and
returned as structured error dicts (rather than raised exceptions) so
Claude can read the error message and adjust. Audit rows are still
written by repo.set_setting() before the rejection.

Discord notification is fired on every successful set and on rejected
operator-only/halal attempts. The post is best-effort — if the webhook
isn't configured the post silently no-ops; the mutation still applies.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §8.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.mcp.server import mcp
from trading_sandwich.notifications.discord import post_card_safe
from trading_sandwich.settings import _halal, _safety_seed, repo
from trading_sandwich.settings.keys import (
    TIER1_HALAL_KEYS,
    TIER2_SAFETY_KEYS,
    tier_of,
)


def _prompt_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/app", text=True
        ).strip()
    except Exception:
        return "unknown"


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
    raise TypeError(f"unsupported value type for {v!r}: {type(v).__name__}")


def _settings_change_card(
    *,
    key: str,
    old_value: Any,
    new_value: Any,
    rationale: str,
    applied: bool,
    rejection_reason: str | None,
) -> dict[str, Any]:
    """Compose a Discord webhook card for a settings change event."""
    if applied:
        title = f":wrench: settings change: `{key}`"
        color = 0x3498DB
        body = f"`{old_value!r}` → `{new_value!r}` (by claude)\n_rationale: {rationale}_"
    else:
        title = f":no_entry: settings change REJECTED: `{key}`"
        color = 0xE74C3C
        body = (
            f"claude tried `{old_value!r}` → `{new_value!r}` "
            f"but was rejected: **{rejection_reason}**\n_rationale: {rationale}_"
        )
    return {
        "embeds": [
            {
                "title": title,
                "description": body,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }


# --- get_setting ----------------------------------------------------------


@mcp.tool()
async def get_setting(key: str) -> dict:
    """Read a single policy setting by dotted-path key.

    Returns a dict with value, value_type, tier (1/2/3), updated_at,
    updated_by. For Tier 1 (halal) keys, value comes from policy.yaml
    and updated_by='file'. For Tier 2/3, value comes from policy_settings
    if a row exists, else from the file seed.

    Returns {error: 'key_not_found'} for unknown keys.
    """
    tier = tier_of(key)

    if tier == 1:
        try:
            value = _halal.read(key)
        except KeyError:
            return {"key": key, "error": "key_not_found"}
        return {
            "key": key,
            "value": value,
            "value_type": _infer_value_type(value),
            "tier": 1,
            "updated_at": None,
            "updated_by": "file",
        }

    # Tier 2 + Tier 3: read from policy_settings
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text(
                    "SELECT value, value_type, updated_at, updated_by "
                    "FROM policy_settings WHERE key = :k"
                ),
                {"k": key},
            )
            row = r.first()
    finally:
        await engine.dispose()

    if row is not None:
        return {
            "key": key,
            "value": row[0],
            "value_type": row[1],
            "tier": tier,
            "updated_at": row[2].isoformat() if row[2] else None,
            "updated_by": row[3],
        }

    # Fallback: Tier 2 reads from the safety seed; Tier 3 has no fallback here
    if tier == 2:
        try:
            value = _safety_seed.read(key)
        except KeyError:
            return {"key": key, "error": "key_not_found"}
        return {
            "key": key,
            "value": value,
            "value_type": _infer_value_type(value),
            "tier": 2,
            "updated_at": None,
            "updated_by": "seed_file",
        }

    return {"key": key, "error": "key_not_found"}


# --- list_settings --------------------------------------------------------


@mcp.tool()
async def list_settings(prefix: str = "") -> list[dict]:
    """List policy settings, optionally filtered by dotted-path prefix.

    Returns rows from policy_settings (Tier 2 + Tier 3). Tier 1 halal
    keys are NOT included here — read them individually via get_setting.
    """
    sql = (
        "SELECT key, value, value_type, updated_at, updated_by "
        "FROM policy_settings"
    )
    params: dict[str, Any] = {}
    if prefix:
        sql += " WHERE key LIKE :p"
        params["p"] = f"{prefix}%"
    sql += " ORDER BY key"

    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(text(sql), params)
            rows = list(r)
    finally:
        await engine.dispose()

    return [
        {
            "key": row[0],
            "value": row[1],
            "value_type": row[2],
            "tier": tier_of(row[0]),
            "updated_at": row[3].isoformat() if row[3] else None,
            "updated_by": row[4],
        }
        for row in rows
    ]


# --- set_setting ----------------------------------------------------------


async def _existing_value_type(key: str) -> str | None:
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text("SELECT value, value_type FROM policy_settings WHERE key = :k"),
                {"k": key},
            )
            row = r.first()
            return (row[0], row[1]) if row else (None, None)
    finally:
        await engine.dispose()


@mcp.tool()
async def set_setting(key: str, value: Any, rationale: str) -> dict:
    """Mutate a policy setting (Claude path).

    Always invokes the repo with authority='mcp_default', changed_by='claude'.
    Tier 1 (halal) and Tier 2 (operator-safety) keys are rejected; the
    rejection is captured and returned as a structured error rather than
    raising. A `policy_changes` audit row is written either way (by the
    repo). Discord notification fires on every outcome.

    `rationale` is required; Claude must populate it from its reasoning.
    """
    if not rationale or not rationale.strip():
        return {
            "applied": False,
            "key": key,
            "error": "missing_rationale",
            "message": "rationale is required and must be non-empty",
        }

    old_value, prior_type = await _existing_value_type(key)
    value_type = prior_type or _infer_value_type(value)
    pv = _prompt_version()

    try:
        result = await repo.set_setting(
            key=key,
            new_value=value,
            value_type=value_type,
            rationale=rationale,
            changed_by="claude",
            authority="mcp_default",
            prompt_version=pv,
        )
    except _halal.HalalViolationError as e:
        await post_card_safe(_settings_change_card(
            key=key, old_value=old_value, new_value=value,
            rationale=rationale, applied=False,
            rejection_reason="halal_inviolable",
        ))
        return {
            "applied": False,
            "key": key,
            "error": "halal_inviolable",
            "message": str(e),
        }
    except repo.OperatorOnlyKeyError as e:
        await post_card_safe(_settings_change_card(
            key=key, old_value=old_value, new_value=value,
            rationale=rationale, applied=False,
            rejection_reason="operator_only_key",
        ))
        return {
            "applied": False,
            "key": key,
            "error": "operator_only_key",
            "message": (
                f"{e}. Use `/safety set` from Discord (operator-only)."
            ),
        }
    except repo.TypeMismatchError as e:
        return {
            "applied": False,
            "key": key,
            "error": "type_mismatch",
            "message": str(e),
        }

    await post_card_safe(_settings_change_card(
        key=key, old_value=result.old_value, new_value=result.new_value,
        rationale=rationale, applied=True, rejection_reason=None,
    ))
    return {
        "applied": True,
        "key": key,
        "old_value": result.old_value,
        "new_value": result.new_value,
        "value_type": value_type,
    }


# --- get_setting_history --------------------------------------------------


@mcp.tool()
async def get_setting_history(key: str, limit: int = 20) -> list[dict]:
    """Return the audit chain for a key, newest first.

    Includes both successful and rejected attempts.
    """
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text(
                    "SELECT id, old_value, new_value, rationale, changed_by, "
                    "authority, applied, rejection_reason, changed_at, "
                    "prompt_version "
                    "FROM policy_changes WHERE key = :k "
                    "ORDER BY changed_at DESC, id DESC LIMIT :n"
                ),
                {"k": key, "n": limit},
            )
            rows = list(r)
    finally:
        await engine.dispose()

    return [
        {
            "id": row[0],
            "old_value": row[1],
            "new_value": row[2],
            "rationale": row[3],
            "changed_by": row[4],
            "authority": row[5],
            "applied": row[6],
            "rejection_reason": row[7],
            "changed_at": row[8].isoformat() if row[8] else None,
            "prompt_version": row[9],
        }
        for row in rows
    ]
