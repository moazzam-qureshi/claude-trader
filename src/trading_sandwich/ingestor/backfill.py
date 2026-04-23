"""REST backfill helpers. Phase 0 ships `expected_candle_opens` only; the
actual REST fetch + gap-scan Celery Beat job is completed in Phase 1.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def expected_candle_opens(start: datetime, end: datetime, timeframe: str) -> list[datetime]:
    """Return the list of expected candle open times in [start, end) for the timeframe."""
    step = timedelta(minutes=_TF_MINUTES[timeframe])
    result: list[datetime] = []
    cur = start
    while cur < end:
        result.append(cur)
        cur += step
    return result
