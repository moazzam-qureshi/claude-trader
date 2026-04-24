from datetime import datetime, timezone
from unittest.mock import MagicMock

from trading_sandwich.triage.daily_cap import (
    check_and_reserve_slot,
    redis_key_for_date,
)


def test_redis_key_format():
    dt = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert redis_key_for_date(dt) == "claude_triage:2026-04-25"


def test_check_and_reserve_first_call_returns_true():
    redis = MagicMock()
    redis.incr.return_value = 1
    dt = datetime(2026, 4, 25, tzinfo=timezone.utc)
    assert check_and_reserve_slot(redis, dt, cap=20) is True
    redis.incr.assert_called_once_with("claude_triage:2026-04-25")
    redis.expire.assert_called_once_with("claude_triage:2026-04-25", 172800)


def test_check_and_reserve_at_cap_returns_true():
    redis = MagicMock()
    redis.incr.return_value = 20
    assert check_and_reserve_slot(redis, datetime(2026, 4, 25, tzinfo=timezone.utc), cap=20) is True


def test_check_and_reserve_over_cap_returns_false():
    redis = MagicMock()
    redis.incr.return_value = 21
    assert check_and_reserve_slot(redis, datetime(2026, 4, 25, tzinfo=timezone.utc), cap=20) is False


def test_check_and_reserve_expire_only_on_first_increment():
    redis = MagicMock()
    redis.incr.return_value = 5
    check_and_reserve_slot(redis, datetime(2026, 4, 25, tzinfo=timezone.utc), cap=20)
    redis.expire.assert_not_called()
