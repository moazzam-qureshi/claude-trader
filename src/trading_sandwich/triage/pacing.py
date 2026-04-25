"""Pacing decision pure function. No DB, no I/O — given inputs, return
PacingDecision."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class PacingConfig:
    min_minutes: int
    max_minutes: int
    daily_cap: int
    weekly_cap: int


@dataclass
class PacingDecision:
    spawn: bool
    actual_interval_min: int | None
    interval_clamped: bool
    exit_reason: str | None


CLAMP_MULTIPLIER = 4


def decide_whether_to_spawn(
    *,
    cfg: PacingConfig,
    last_spawned_at: datetime | None,
    last_requested_interval_min: int | None,
    spawned_today: int,
    spawned_this_week: int,
    now: datetime | None = None,
) -> PacingDecision:
    now = now or datetime.now(timezone.utc)

    if last_spawned_at is None:
        return PacingDecision(
            spawn=True,
            actual_interval_min=None,
            interval_clamped=False,
            exit_reason=None,
        )

    actual = int((now - last_spawned_at).total_seconds() // 60)
    requested = last_requested_interval_min or cfg.min_minutes

    if actual < requested:
        return PacingDecision(
            spawn=False,
            actual_interval_min=actual,
            interval_clamped=False,
            exit_reason="too_soon",
        )

    if spawned_today >= cfg.daily_cap:
        return PacingDecision(
            spawn=False,
            actual_interval_min=actual,
            interval_clamped=False,
            exit_reason="daily_cap_hit",
        )

    if spawned_this_week >= cfg.weekly_cap:
        return PacingDecision(
            spawn=False,
            actual_interval_min=actual,
            interval_clamped=False,
            exit_reason="weekly_cap_hit",
        )

    clamped = requested > 0 and actual >= requested * CLAMP_MULTIPLIER
    return PacingDecision(
        spawn=True,
        actual_interval_min=actual,
        interval_clamped=clamped,
        exit_reason=None,
    )
