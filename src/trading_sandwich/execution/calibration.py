"""Calibration query — median 24h return by decision class."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision, SignalOutcome


async def calibration_report(lookback_days: int = 30) -> dict:
    """Return median 24h-horizon return_pct for alert vs ignore decisions."""
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(ClaudeDecision.decision, SignalOutcome.return_pct)
            .join(SignalOutcome, SignalOutcome.signal_id == ClaudeDecision.signal_id)
            .where(
                ClaudeDecision.invocation_mode == "triage",
                ClaudeDecision.invoked_at >= since,
                SignalOutcome.horizon == "24h",
            )
        )).all()
    by_decision: dict[str, list[float]] = {}
    for d, r in rows:
        by_decision.setdefault(d, []).append(float(r))
    return {
        "lookback_days": lookback_days,
        "alert_median_24h": median(by_decision.get("alert", [])) if by_decision.get("alert") else None,
        "ignore_median_24h": median(by_decision.get("ignore", [])) if by_decision.get("ignore") else None,
        "alert_count": len(by_decision.get("alert", [])),
        "ignore_count": len(by_decision.get("ignore", [])),
    }
