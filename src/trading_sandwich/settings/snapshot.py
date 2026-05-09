"""Decision-time policy snapshot.

`snapshot_policy()` runs once at the start of every Claude shift /
portfolio-strategist invocation and returns the full effective policy
state. The returned dict is persisted into `claude_decisions.policy_snapshot`
or `portfolio_decisions.policy_snapshot` so any decision row can be
fully reproduced from disk: given the snapshot, you can exactly recreate
what Claude was looking at when it decided.

Composition:
    {
      "settings":   <every row in policy_settings — Tier 2 + Tier 3>,
      "inviolable": <every Tier 1 halal value, read from policy.yaml>,
      "snapshot_at": ISO8601 UTC timestamp,
      "git_head":   full SHA from `git rev-parse HEAD`, or 'unknown'
                    if git is unavailable.
    }

See docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md §10.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.settings import _halal


def _git_head() -> str:
    """Return the full SHA of HEAD, or 'unknown' if git isn't available."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/app", text=True
        ).strip()
    except Exception:
        return "unknown"


async def _read_all_settings() -> dict[str, Any]:
    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            r = await conn.execute(text("SELECT key, value FROM policy_settings"))
            return {row[0]: row[1] for row in r}
    finally:
        await engine.dispose()


async def snapshot_policy() -> dict[str, Any]:
    """Return the full effective policy snapshot for embedding in a decision row.

    Reads policy_settings (DB) and Tier 1 halal values (file) and merges them
    with provenance metadata. ~5KB per snapshot at expected key counts.
    """
    settings_block = await _read_all_settings()
    inviolable_block = _halal.read_all()
    return {
        "settings": settings_block,
        "inviolable": inviolable_block,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
    }
