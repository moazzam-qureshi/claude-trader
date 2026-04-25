"""Discord webhook notifier for universe events."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx


WEBHOOK_ENV = "DISCORD_UNIVERSE_WEBHOOK_URL"


def _webhook_url() -> str:
    url = os.environ.get(WEBHOOK_ENV)
    if not url:
        raise RuntimeError(f"{WEBHOOK_ENV} not set")
    return url


def render_universe_event_card(
    *,
    occurred_at: datetime,
    event_type: str,
    symbol: str,
    from_tier: str | None,
    to_tier: str | None,
    rationale: str,
    reversion_criterion: str | None,
    shift_id: int | None,
    diary_ref: str | None,
) -> dict[str, Any]:
    movement = (
        f"{from_tier} → {to_tier}" if from_tier and to_tier
        else f"→ {to_tier}" if to_tier
        else f"from {from_tier}" if from_tier
        else ""
    )
    title_line = f"🔄 Universe change — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}"
    headline = f"**{symbol} {movement} ({event_type})**"
    parts = [title_line, headline, "", f"Rationale: {rationale}"]
    if reversion_criterion:
        parts.append(f"Reversion: {reversion_criterion}")
    meta = []
    if shift_id is not None:
        meta.append(f"shift_id: {shift_id}")
    if diary_ref:
        meta.append(f"diary: {diary_ref}")
    if meta:
        parts.append(" · ".join(meta))
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_hard_limit_blocked_card(
    *,
    occurred_at: datetime,
    attempted: dict[str, Any],
    blocked_by: str,
) -> dict[str, Any]:
    movement = (
        f"{attempted.get('from_tier', '?')} → {attempted.get('to_tier', '?')}"
        if attempted.get("from_tier") or attempted.get("to_tier")
        else ""
    )
    parts = [
        f"⛔ Hard limit blocked — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Claude attempted: **{attempted.get('event_type')} {attempted.get('symbol')}** {movement}",
        f"Blocked by: `{blocked_by}`",
        "",
        f"Rationale: {(attempted.get('rationale') or '')[:200]}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


async def post_card(card: dict[str, Any]) -> str | None:
    """POST card to Discord webhook. Returns Discord message_id on success,
    None on failure."""
    url = _webhook_url()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{url}?wait=true", json=card)
            if resp.status_code >= 400:
                return None
            data = resp.json()
            return data.get("id")
    except Exception:
        return None
