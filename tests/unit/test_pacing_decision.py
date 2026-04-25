from datetime import datetime, timedelta, timezone

from trading_sandwich.triage.pacing import (
    PacingConfig,
    decide_whether_to_spawn,
)


CFG = PacingConfig(
    min_minutes=15,
    max_minutes=240,
    daily_cap=60,
    weekly_cap=350,
)


def _ts(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_first_ever_shift_spawns():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=None,
        last_requested_interval_min=None,
        spawned_today=0,
        spawned_this_week=0,
    )
    assert d.spawn is True
    assert d.exit_reason is None


def test_too_soon_does_not_spawn():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(10),
        last_requested_interval_min=30,
        spawned_today=5,
        spawned_this_week=20,
    )
    assert d.spawn is False
    assert d.exit_reason == "too_soon"


def test_after_requested_interval_spawns():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(35),
        last_requested_interval_min=30,
        spawned_today=5,
        spawned_this_week=20,
    )
    assert d.spawn is True
    assert d.actual_interval_min == 35
    assert d.interval_clamped is False


def test_daily_cap_blocks_spawn():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(60),
        last_requested_interval_min=30,
        spawned_today=60,
        spawned_this_week=200,
    )
    assert d.spawn is False
    assert d.exit_reason == "daily_cap_hit"


def test_weekly_cap_blocks_spawn():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(60),
        last_requested_interval_min=30,
        spawned_today=10,
        spawned_this_week=350,
    )
    assert d.spawn is False
    assert d.exit_reason == "weekly_cap_hit"


def test_clamp_set_when_actual_much_larger_than_requested():
    """Asked for 15min, didn't get spawned for 120min → clamped flag."""
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(120),
        last_requested_interval_min=15,
        spawned_today=5,
        spawned_this_week=20,
    )
    assert d.spawn is True
    assert d.interval_clamped is True
