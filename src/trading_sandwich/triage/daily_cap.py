"""Daily triage cap enforcement via date-keyed Redis counter.

Atomic INCR with EXPIRE only on first increment (count==1). No separate
reset task — old keys age out via EXPIRE.
"""
from __future__ import annotations

from datetime import datetime


_EXPIRE_SECONDS = 172800  # 48h


def redis_key_for_date(now: datetime) -> str:
    return f"claude_triage:{now.strftime('%Y-%m-%d')}"


def check_and_reserve_slot(redis_client, now: datetime, cap: int) -> bool:
    """Atomically reserve one slot for the given UTC day.

    Returns True if the reservation succeeded (count <= cap), False if the
    cap has been exceeded.
    """
    key = redis_key_for_date(now)
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, _EXPIRE_SECONDS)
    return count <= cap
