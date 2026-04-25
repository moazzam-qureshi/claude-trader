"""Position watchdog — Celery Beat task running every 60s.

Compares adapter-reported open positions against the local positions table.
Drift > tolerance triggers a kill-switch + risk_events row + Discord alert.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from trading_sandwich import _policy
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Position, RiskEvent


async def _adapter_positions() -> list[dict]:
    from trading_sandwich.execution.worker import _adapter
    adapter, _ = _adapter()
    return await adapter.get_positions()


async def _local_positions() -> list[dict]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(Position).where(Position.closed_at.is_(None))
        )).scalars().all()
        return [{"symbol": r.symbol, "size_base": str(r.size_base)} for r in rows]


async def reconcile_async() -> None:
    adapter_pos = {p["symbol"]: p for p in await _adapter_positions()}
    local_pos = {p["symbol"]: p for p in await _local_positions()}

    drifts = []
    for sym in set(adapter_pos.keys()) | set(local_pos.keys()):
        a = adapter_pos.get(sym)
        loc = local_pos.get(sym)
        if (a is None) != (loc is None):
            drifts.append({"symbol": sym, "adapter": a, "local": loc,
                           "kind": "presence_drift"})
            continue
        if a and loc and str(a["size_base"]) != str(loc["size_base"]):
            drifts.append({"symbol": sym, "adapter": a, "local": loc,
                           "kind": "size_drift"})

    if not drifts:
        return

    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for d in drifts:
            session.add(RiskEvent(
                event_id=uuid4(),
                kind="reconciliation_" + d["kind"],
                severity="warning",
                context=d,
                action_taken="logged",
                at=now,
            ))
        await session.commit()

    tol = _policy.get_reconciliation_block_tolerance()
    if len(drifts) > int(tol.get("open_order_count_drift", 0)):
        from trading_sandwich.execution.kill_switch import trip
        await trip(reason=f"reconciliation_drift_{len(drifts)}")


@app.task(name="trading_sandwich.execution.watchdog.reconcile")
def reconcile() -> None:
    asyncio.run(reconcile_async())
