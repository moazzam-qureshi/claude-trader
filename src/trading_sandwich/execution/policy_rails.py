"""Pre-trade policy rails. Task 29 implements all 16 rails; this stub
returns None (no block) so Task 25 can land paper-fill behavior first."""
from __future__ import annotations

from uuid import UUID


async def evaluate_policy(proposal) -> str | None:
    """Returns None to allow, or a block reason string to deny."""
    return None


async def record_risk_event(proposal_id: UUID, reason: str) -> None:
    """Logs a risk event. Task 29 fleshes this out."""
    pass
