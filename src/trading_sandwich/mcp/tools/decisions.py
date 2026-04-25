"""save_decision MCP tool."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert

from trading_sandwich.contracts.phase2 import AlertPayload, DecisionLiteral
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.mcp.server import mcp

_ALLOWED = {"alert", "paper_trade", "ignore", "research_more"}


def _capture_prompt_version() -> str:
    env = os.environ.get("TS_PROMPT_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/workspace"
        ).decode().strip()
    except Exception:
        return "unknown"


@mcp.tool()
async def save_decision(
    signal_id: UUID,
    decision: DecisionLiteral,
    rationale: str,
    alert_payload: AlertPayload | None = None,
    notes: str | None = None,
) -> UUID:
    """Persist one claude_decisions row. Idempotent on (signal_id, invocation_mode)."""
    if decision == "live_order":
        raise ValueError(
            "live_order is not a valid Phase 2 decision; propose_trade instead"
        )
    if decision not in _ALLOWED:
        raise ValueError(f"invalid decision {decision!r}; allowed: {sorted(_ALLOWED)}")
    if len(rationale) < 40:
        raise ValueError("rationale must be at least 40 characters")
    if decision == "alert" and alert_payload is None:
        raise ValueError("alert_payload is required when decision='alert'")

    now = datetime.now(timezone.utc)
    decision_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        stmt = insert(ClaudeDecision).values(
            decision_id=decision_id,
            signal_id=signal_id,
            invocation_mode="triage",
            invoked_at=now,
            completed_at=now,
            prompt_version=_capture_prompt_version(),
            decision=decision,
            rationale=rationale,
            output={"notes": notes} if notes else None,
        ).on_conflict_do_update(
            index_elements=["signal_id", "invocation_mode"],
            set_={
                "decision": decision,
                "rationale": rationale,
                "completed_at": now,
                "prompt_version": _capture_prompt_version(),
                "output": {"notes": notes} if notes else None,
            },
        ).returning(ClaudeDecision.decision_id)
        result = await session.execute(stmt)
        returned = result.scalar_one()
        await session.commit()
    return returned
