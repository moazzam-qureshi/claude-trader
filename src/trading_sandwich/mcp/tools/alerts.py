"""send_alert MCP tool."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from trading_sandwich.config import get_settings
from trading_sandwich.contracts.phase2 import AlertPayload
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Alert
from trading_sandwich.discord.webhook import post_webhook
from trading_sandwich.mcp.server import mcp


@mcp.tool()
async def send_alert(channel: Literal["discord"], payload: AlertPayload) -> UUID:
    """Idempotent alert send (UNIQUE on (signal_id, channel))."""
    if channel != "discord":
        raise ValueError(f"unsupported channel {channel!r}")

    now = datetime.now(timezone.utc)
    alert_id = uuid4()

    factory = get_session_factory()
    async with factory() as session:
        stmt = insert(Alert).values(
            alert_id=alert_id,
            signal_id=payload.signal_id,
            decision_id=payload.decision_id,
            channel=channel,
            sent_at=now,
            payload=payload.model_dump(mode="json"),
            delivered=False,
        ).on_conflict_do_nothing(index_elements=["signal_id", "channel"]).returning(Alert.alert_id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            row = (await session.execute(
                select(Alert).where(Alert.signal_id == payload.signal_id, Alert.channel == channel)
            )).scalar_one()
            await session.commit()
            return row.alert_id
        await session.commit()

    settings = get_settings()
    if settings.discord_webhook_url:
        delivered = False
        err: str | None = None
        try:
            status = await post_webhook(settings.discord_webhook_url, {
                "embeds": [{"title": payload.title, "description": payload.body}],
            })
            delivered = 200 <= status < 300
            err = None if delivered else f"http_{status}"
        except Exception as exc:
            err = str(exc)[:500]

        async with factory() as session:
            await session.execute(
                update(Alert).where(Alert.alert_id == alert_id).values(
                    delivered=delivered, error=err
                )
            )
            await session.commit()

    return alert_id
