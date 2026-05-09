"""Pure async handlers for Discord /settings + /safety slash commands.

These functions take string args (as Discord delivers them) plus the
actor's user ID and the configured operator ID. They:

  - validate authority structurally (operator-only for /safety),
  - parse the string value into the right Python type,
  - call repo.set_setting() with the correct authority+changed_by,
  - return a markdown-formatted reply for Discord.

The split between /settings and /safety is structural, not just a check:

  /settings set  -> always passes authority='mcp_default'
                    Tier 2 keys come back operator_only_key with a
                    redirect message to /safety set. Operator using
                    /settings set on a circuit breaker by accident is
                    structurally prevented from succeeding.

  /safety set    -> verifies actor_id == operator_id
                    Non-operator -> writes rejected policy_changes audit
                                    row with rejection_reason='not_operator'.
                                    Repo never invoked.
                    Operator -> passes authority='operator_safety'.
                                Tier 1 still rejected (halal).
                                Tier 3 rejected with redirect to /settings.

The 9 safety-critical settings_repo_set tests remain authoritative for
the runtime mutation gate.

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md \xc2\xa79.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
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


# --- value coercion -------------------------------------------------------


def _coerce(value_str: str, value_type: str | None) -> Any:
    """Coerce a Discord-supplied string into the right Python value.

    If value_type is known (existing row), coerce strictly to it. If not,
    attempt int -> float -> bool -> string.
    """
    if value_type == "bool":
        s = value_str.strip().lower()
        if s in ("true", "yes", "y", "on", "1"):
            return True
        if s in ("false", "no", "n", "off", "0"):
            return False
        raise ValueError(f"cannot parse {value_str!r} as bool")
    if value_type == "int":
        return int(value_str)
    if value_type == "float":
        return float(value_str)
    if value_type == "string":
        return value_str
    if value_type == "array" or value_type == "object":
        return json.loads(value_str)

    # Type-free guess
    try:
        return int(value_str)
    except ValueError:
        pass
    try:
        return float(value_str)
    except ValueError:
        pass
    s = value_str.strip().lower()
    if s in ("true", "false"):
        return s == "true"
    return value_str


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
    raise TypeError(f"unsupported value type: {type(v).__name__}")


# --- DB helpers -----------------------------------------------------------


async def _existing_row(key: str) -> tuple[Any, str | None]:
    """Return (current_value, current_value_type) or (None, None)."""
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


async def _write_rejected_audit(
    *, key: str, attempted_value: Any, rationale: str,
    changed_by: str, authority: str, rejection_reason: str,
    prompt_version: str | None,
) -> None:
    """Insert a policy_changes row for an attempt that the handler rejected
    BEFORE calling repo.set_setting() (e.g. not_operator)."""
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO policy_changes "
                    "(key, old_value, new_value, rationale, changed_by, "
                    "authority, applied, rejection_reason, prompt_version) "
                    "VALUES (:k, NULL, CAST(:nv AS jsonb), :r, :cb, :a, "
                    "false, :rr, :pv)"
                ),
                {
                    "k": key,
                    "nv": json.dumps(attempted_value),
                    "r": rationale,
                    "cb": changed_by,
                    "a": authority,
                    "rr": rejection_reason,
                    "pv": prompt_version,
                },
            )
    finally:
        await engine.dispose()


async def _delete_row_with_audit(
    *, key: str, seed_value: Any, changed_by: str, authority: str,
    rationale: str, prompt_version: str | None,
) -> Any | None:
    """Delete policy_settings row and write a successful audit row.

    Returns old_value if a row existed.
    """
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            r = await conn.execute(
                text("DELETE FROM policy_settings WHERE key = :k RETURNING value"),
                {"k": key},
            )
            row = r.first()
            old = row[0] if row else None
            await conn.execute(
                text(
                    "INSERT INTO policy_changes "
                    "(key, old_value, new_value, rationale, changed_by, "
                    "authority, applied, rejection_reason, prompt_version) "
                    "VALUES (:k, CAST(:ov AS jsonb), CAST(:nv AS jsonb), :r, "
                    ":cb, :a, true, NULL, :pv)"
                ),
                {
                    "k": key,
                    "ov": json.dumps(old) if old is not None else None,
                    "nv": json.dumps(seed_value),
                    "r": rationale,
                    "cb": changed_by,
                    "a": authority,
                    "pv": prompt_version,
                },
            )
            return old
    finally:
        await engine.dispose()


def _settings_change_card(
    *, key: str, old_value: Any, new_value: Any, rationale: str,
    actor: str, applied: bool, rejection_reason: str | None,
) -> dict[str, Any]:
    if applied:
        title = f":wrench: settings change: `{key}`"
        color = 0x3498DB
        body = f"`{old_value!r}` → `{new_value!r}` (by {actor})\n_rationale: {rationale}_"
    else:
        title = f":no_entry: settings change REJECTED: `{key}`"
        color = 0xE74C3C
        body = (
            f"{actor} tried `{old_value!r}` → `{new_value!r}` "
            f"but was rejected: **{rejection_reason}**\n_rationale: {rationale}_"
        )
    return {"embeds": [{"title": title, "description": body, "color": color}]}


# --- /settings list / get -------------------------------------------------


async def handle_settings_list(prefix: str = "") -> str:
    sql = "SELECT key, value, value_type FROM policy_settings"
    params: dict[str, Any] = {}
    if prefix:
        sql += " WHERE key LIKE :p"
        params["p"] = f"{prefix}%"
    sql += " ORDER BY key LIMIT 200"

    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(text(sql), params)
            rows = list(r)
    finally:
        await engine.dispose()

    if not rows:
        return f"_no settings under prefix `{prefix}`_"
    lines = [f"**settings** (prefix=`{prefix or '*'}`, showing {len(rows)}):"]
    for k, v, t in rows:
        tier = tier_of(k)
        marker = "T1" if tier == 1 else ("T2" if tier == 2 else "T3")
        lines.append(f"- `{k}` = `{v!r}` ({t}, {marker})")
    return "\n".join(lines)


async def handle_settings_get(key: str) -> str:
    tier = tier_of(key)
    if tier == 1:
        try:
            v = _halal.read(key)
        except KeyError:
            return f"key_not_found: `{key}`"
        return f"`{key}` = `{v!r}` (Tier 1 halal, file-only — inviolable)"

    val, vtype = await _existing_row(key)
    if val is None:
        if tier == 2:
            try:
                v = _safety_seed.read(key)
            except KeyError:
                return f"key_not_found: `{key}`"
            return f"`{key}` = `{v!r}` (Tier 2 safety, file seed — no DB override)"
        return f"key_not_found: `{key}`"

    marker = "Tier 2 safety" if tier == 2 else "Tier 3"
    return f"`{key}` = `{val!r}` ({vtype}, {marker})"


# --- /settings set --------------------------------------------------------


async def handle_settings_set(
    key: str, value_str: str, rationale: str
) -> str:
    """Tier 3 mutation surface (the operator-via-/settings path).

    Always passes authority='mcp_default', changed_by='operator'. A Tier 2
    key here is rejected with a redirect to /safety set. Tier 1 is
    rejected as halal_inviolable.
    """
    if not rationale.strip():
        return ":warning: rationale required"

    old_value, prior_type = await _existing_row(key)
    try:
        value = _coerce(value_str, prior_type)
        value_type = prior_type or _infer_value_type(value)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return f":warning: cannot parse value: {e}"

    pv = _prompt_version()
    try:
        result = await repo.set_setting(
            key=key, new_value=value, value_type=value_type,
            rationale=rationale, changed_by="operator",
            authority="mcp_default", prompt_version=pv,
        )
    except _halal.HalalViolationError as e:
        await post_card_safe(_settings_change_card(
            key=key, old_value=old_value, new_value=value, rationale=rationale,
            actor="operator", applied=False, rejection_reason="halal_inviolable",
        ))
        return f":no_entry: halal_inviolable: {e}"
    except repo.OperatorOnlyKeyError:
        await post_card_safe(_settings_change_card(
            key=key, old_value=old_value, new_value=value, rationale=rationale,
            actor="operator", applied=False, rejection_reason="operator_only_key",
        ))
        return (
            f":no_entry: `{key}` is Tier 2 (operator-safety). "
            f"Use `/safety set` instead."
        )
    except repo.TypeMismatchError as e:
        return f":warning: type_mismatch: {e}"

    await post_card_safe(_settings_change_card(
        key=key, old_value=result.old_value, new_value=result.new_value,
        rationale=rationale, actor="operator", applied=True,
        rejection_reason=None,
    ))
    return f":white_check_mark: applied: `{key}` `{result.old_value!r}` → `{result.new_value!r}`"


# --- /safety: operator-only Tier 2 surface --------------------------------


async def handle_safety_list() -> str:
    lines = ["**safety (Tier 2) keys** — operator-only via `/safety set`:"]
    for k in sorted(TIER2_SAFETY_KEYS):
        try:
            seed_v = _safety_seed.read(k)
        except KeyError:
            seed_v = "<missing-seed>"
        db_v, _ = await _existing_row(k)
        if db_v is None:
            lines.append(f"- `{k}` = `{seed_v!r}` (file seed; no DB override)")
        else:
            lines.append(
                f"- `{k}` = `{db_v!r}` (DB override; file seed was `{seed_v!r}`)"
            )
    return "\n".join(lines)


async def handle_safety_set(
    *, actor_id: str, operator_id: str,
    key: str, value_str: str, rationale: str,
) -> str:
    """Tier 2 mutation surface — operator-only.

    Authority handoff:
      - actor_id != operator_id -> not_operator audit row, no mutation.
      - Tier 2 + operator -> repo.set_setting(authority='operator_safety').
      - Tier 1 -> halal_inviolable from repo (still routed through repo so
        the audit row gets the standard halal_inviolable reason).
      - Tier 3 -> rejected here (use /settings set), no DB write.
    """
    if not rationale.strip():
        return ":warning: rationale required"

    pv = _prompt_version()
    tier = tier_of(key)

    # Try to coerce; if we can't and it's a bad input we still want to
    # return cleanly without writing audit.
    try:
        old_value, prior_type = await _existing_row(key)
        value = _coerce(value_str, prior_type)
        value_type = prior_type or _infer_value_type(value)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return f":warning: cannot parse value: {e}"

    # SAFETY CRITICAL: non-operator path. Audit row, NO repo invocation.
    if actor_id != operator_id:
        await _write_rejected_audit(
            key=key, attempted_value=value, rationale=rationale,
            changed_by="operator", authority="operator_safety",
            rejection_reason="not_operator", prompt_version=pv,
        )
        await post_card_safe(_settings_change_card(
            key=key, old_value=old_value, new_value=value, rationale=rationale,
            actor=f"non-operator user {actor_id}", applied=False,
            rejection_reason="not_operator",
        ))
        return f":no_entry: not authorized — actor `{actor_id}` is not the configured operator"

    # Tier 3 keys belong on /settings set
    if tier == 3:
        return (
            f":no_entry: `{key}` is Tier 3. Use `/settings set` for non-safety keys."
        )

    try:
        result = await repo.set_setting(
            key=key, new_value=value, value_type=value_type,
            rationale=rationale, changed_by="operator",
            authority="operator_safety", prompt_version=pv,
        )
    except _halal.HalalViolationError as e:
        await post_card_safe(_settings_change_card(
            key=key, old_value=old_value, new_value=value, rationale=rationale,
            actor="operator", applied=False, rejection_reason="halal_inviolable",
        ))
        return f":no_entry: halal_inviolable: {e}"
    except repo.TypeMismatchError as e:
        return f":warning: type_mismatch: {e}"

    await post_card_safe(_settings_change_card(
        key=key, old_value=result.old_value, new_value=result.new_value,
        rationale=rationale, actor="operator", applied=True,
        rejection_reason=None,
    ))
    return f":white_check_mark: applied: `{key}` `{result.old_value!r}` → `{result.new_value!r}`"


async def handle_safety_reset(
    *, actor_id: str, operator_id: str, key: str,
) -> str:
    """Delete a Tier 2 DB override so the file seed wins again."""
    if actor_id != operator_id:
        await _write_rejected_audit(
            key=key, attempted_value=None, rationale="reset",
            changed_by="operator", authority="operator_safety",
            rejection_reason="not_operator", prompt_version=_prompt_version(),
        )
        return f":no_entry: not authorized — actor `{actor_id}` is not the configured operator"

    if key not in TIER2_SAFETY_KEYS:
        return f":no_entry: `{key}` is not a Tier 2 safety key. Reset only applies to /safety keys."

    try:
        seed_value = _safety_seed.read(key)
    except KeyError:
        return f":warning: no file seed exists for `{key}`; cannot reset"

    old = await _delete_row_with_audit(
        key=key, seed_value=seed_value, changed_by="operator",
        authority="operator_safety", rationale="reset to file seed",
        prompt_version=_prompt_version(),
    )

    await post_card_safe(_settings_change_card(
        key=key, old_value=old, new_value=seed_value,
        rationale="reset to file seed", actor="operator", applied=True,
        rejection_reason=None,
    ))
    return f":arrows_counterclockwise: reset `{key}` to file seed `{seed_value!r}` (was `{old!r}`)"
