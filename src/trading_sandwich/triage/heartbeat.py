"""Heartbeat scheduler — gating worker + Celery task wrapper.

Pattern: Celery Beat fires every 15 min. The task reads STATE.md and the
heartbeat_shifts history, decides whether to spawn Claude (per pacing rules),
and either spawns + records, or records a skipped row and exits.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift


POLICY_PATH = Path(os.environ.get("TS_POLICY_PATH", "/app/policy.yaml"))


@dataclass
class PacingInputs:
    last_spawned_at: datetime | None
    last_requested_interval_min: int | None
    spawned_today: int
    spawned_this_week: int


def _prompt_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd="/app"
        ).strip()
    except Exception:
        return "unknown"


async def _query_pacing_inputs() -> PacingInputs:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    async with factory() as session:
        last = (await session.execute(
            select(HeartbeatShift)
            .where(HeartbeatShift.spawned.is_(True))
            .order_by(HeartbeatShift.started_at.desc())
            .limit(1)
        )).scalars().first()
        spawned_today = (await session.execute(
            select(func.count(HeartbeatShift.id))
            .where(
                HeartbeatShift.spawned.is_(True),
                HeartbeatShift.started_at >= today_start,
            )
        )).scalar_one()
        spawned_this_week = (await session.execute(
            select(func.count(HeartbeatShift.id))
            .where(
                HeartbeatShift.spawned.is_(True),
                HeartbeatShift.started_at >= week_start,
            )
        )).scalar_one()
    return PacingInputs(
        last_spawned_at=last.started_at if last else None,
        last_requested_interval_min=last.next_check_in_minutes if last else None,
        spawned_today=spawned_today,
        spawned_this_week=spawned_this_week,
    )


async def record_skipped_shift(
    *,
    actual_interval_min: int | None,
    exit_reason: str,
    prompt_version: str,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(HeartbeatShift(
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            actual_interval_min=actual_interval_min,
            spawned=False,
            exit_reason=exit_reason,
            prompt_version=prompt_version,
        ))
        await session.commit()


def load_pacing_config():
    from trading_sandwich.triage.pacing import PacingConfig
    raw = yaml.safe_load(POLICY_PATH.read_text())
    hb = raw["heartbeat"]
    return PacingConfig(
        min_minutes=hb["interval_minutes"]["min"],
        max_minutes=hb["interval_minutes"]["max"],
        daily_cap=hb["daily_shift_cap"],
        weekly_cap=hb["weekly_shift_cap"],
    )
