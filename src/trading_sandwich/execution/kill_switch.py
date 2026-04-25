"""Kill-switch state — persisted singleton row that survives worker restart."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import KillSwitchState


async def is_active() -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(KillSwitchState).where(KillSwitchState.id == 1)
        )).scalar_one_or_none()
    return bool(row.active) if row else False


async def _update_state(active: bool, reason_or_ack: str) -> None:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        if active:
            await session.execute(
                update(KillSwitchState)
                .where(KillSwitchState.id == 1)
                .values(active=True, tripped_at=now, tripped_reason=reason_or_ack)
            )
        else:
            await session.execute(
                update(KillSwitchState)
                .where(KillSwitchState.id == 1)
                .values(active=False, resumed_at=now, resumed_ack_reason=reason_or_ack)
            )
        await session.commit()


async def trip(reason: str) -> None:
    """Trip the kill-switch. Writes the persisted row."""
    if not reason:
        raise ValueError("reason is required")
    await _update_state(True, reason)


async def resume(ack_reason: str) -> None:
    """Resume from kill-switch. Manual operator action only."""
    if not ack_reason or len(ack_reason) < 4:
        raise ValueError("ack_reason is required (>=4 chars)")
    await _update_state(False, ack_reason)
