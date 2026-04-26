"""MCP tools: read_diary, write_state, append_diary."""
from __future__ import annotations

import os
from datetime import date as _date
from pathlib import Path

from trading_sandwich.contracts.heartbeat import StateFrontmatter
from trading_sandwich.mcp.server import mcp
from trading_sandwich.triage.state_io import (
    StateIOError,
    append_diary as _append_diary_file,
    diary_path_for,
    write_state as _write_state_file,
)


def _diary_dir() -> Path:
    return Path(os.environ.get("TS_DIARY_DIR", "/app/runtime/diary"))


def _state_path() -> Path:
    return Path(os.environ.get("TS_STATE_PATH", "/app/runtime/STATE.md"))


def _today() -> _date:
    override = os.environ.get("TS_TODAY_OVERRIDE")
    if override:
        return _date.fromisoformat(override)
    return _date.today()


@mcp.tool()
async def read_diary(date: str, max_chars: int = 8000) -> dict:
    """Return the contents of `diary/<date>.md`. Empty content if file missing."""
    path = _diary_dir() / f"{date}.md"
    if not path.exists():
        return {"date": date, "content": "", "truncated": False}
    content = path.read_text(encoding="utf-8")
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {"date": date, "content": content, "truncated": truncated}


@mcp.tool()
async def write_state(body: str, frontmatter: dict) -> dict:
    """Replace runtime/STATE.md with provided frontmatter + body."""
    try:
        fm = StateFrontmatter.model_validate(frontmatter)
    except Exception as exc:
        return {"written": False, "body_truncated": False, "error": str(exc)}
    try:
        result = _write_state_file(_state_path(), fm, body)
    except StateIOError as exc:
        return {"written": False, "body_truncated": False, "error": str(exc)}
    return {"written": True, "body_truncated": result.body_truncated, "error": None}


@mcp.tool()
async def append_diary(entry: str) -> dict:
    """Append an entry to today's diary file."""
    path = diary_path_for(_diary_dir(), _today())
    _append_diary_file(path, entry)
    return {"appended": True, "file": str(path)}


@mcp.tool()
async def notify_operator(
    title: str,
    body: str,
    severity: str = "info",
) -> dict:
    """Post a Discord card to the operator's universe-events channel.

    Use sparingly. The operator sees every notification you send. Use this
    when you have something specific the operator should know — a thesis
    you want surfaced, a structural concern, a setup you'd take if not
    blocked, a request for a manual decision, or a mistake you noticed in
    a prior shift. **Do not use for routine OBSERVE shifts** — that's what
    the diary is for.

    Args:
        title: short headline, will appear bold (max 100 chars)
        body:  the message itself (max 1500 chars; longer is truncated)
        severity: one of:
          - 'info'      💬 — general note, default
          - 'watching'  👀 — flagging a setup forming, no action needed yet
          - 'thinking'  🧠 — sharing a thesis or hypothesis
          - 'concern'   ⚠️ — something looks wrong, operator should look
          - 'alert'     🚨 — operator action recommended now
          - 'success'   🎉 — milestone or good outcome

    Returns: {posted: bool}. Discord failure does NOT raise.
    """
    from datetime import datetime, timezone
    from trading_sandwich.notifications.discord import (
        post_card_safe, render_trader_note_card,
    )
    valid = {"info", "watching", "thinking", "concern", "alert", "success"}
    if severity not in valid:
        severity = "info"
    title = (title or "")[:100].strip() or "(no title)"
    body = (body or "")[:1500].strip() or "(empty)"

    await post_card_safe(render_trader_note_card(
        occurred_at=datetime.now(timezone.utc),
        severity=severity,
        title=title,
        body=body,
    ))
    return {"posted": True}
