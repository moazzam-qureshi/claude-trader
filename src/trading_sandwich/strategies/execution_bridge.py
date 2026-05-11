"""Strategy → execution-rail bridge — Blocker B (path to production).

The strategy worker collects `OrderIntent`s from `tick()`; this module
turns each into a real order on the execution rail:

  intent → OrderRequest → strategy-intent rail subset → adapter.submit_order
         → persist an `orders` row + a `strategy_orders` row

A rail block produces no order — a `risk_events` row is written instead
and the intent is dropped (the strategy isn't errored; a strategy that
somehow emits something the rails refuse should be stopped at the gate,
not crashed). The mode (paper / live) is the same `_adapter()` switch
the proposal path uses, gated by `execution_mode` + key presence.

Strategies carry no `StopLossSpec` — a long-only spot limit-buy below
market with no leverage is intrinsically risk-bounded, and the strategy
already bounds its own capital. We attach a `structural` no-op stop so
the `OrderRequest` contract is satisfied; the strategy-intent rail path
deliberately omits the stop-loss rails (see policy_rails).

Idempotency: `orders.client_order_id` is unique, so re-submitting the
same intent's client_order_id (a re-emit, e.g. a grid resting order
that hasn't filled) is a no-op — we look it up first and skip.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from trading_sandwich.contracts.phase2 import OrderRequest, StopLossSpec
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Order, RiskEvent
from trading_sandwich.strategies.base import OrderIntent
from trading_sandwich.strategies.repo import StrategyRow


logger = logging.getLogger(__name__)


# A no-op protective stop. The strategy-intent rail path omits the
# stop-loss rails; this exists only so the OrderRequest contract holds.
_NOOP_STOP = StopLossSpec(kind="structural", value=0)


def _policy_version(strategy_row: StrategyRow) -> str:
    if strategy_row.prompt_version:
        return strategy_row.prompt_version
    env = os.environ.get("TS_PROMPT_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/app",
        ).decode().strip()
    except Exception:
        return "unknown"


def intent_to_order_request(intent: OrderIntent) -> OrderRequest:
    """Translate an OrderIntent into an OrderRequest. `side` maps
    directly (both long-only). `direction` (buy/sell) isn't part of
    OrderRequest yet — it's carried for the fill-back loop via the
    strategy_orders.role link; the adapter only needs side + type +
    size + (limit) price."""
    return OrderRequest(
        symbol=intent.symbol,
        side=intent.side,  # always 'long' — halal-spot inviolable
        order_type=intent.order_type,
        size_usd=intent.size_usd,
        limit_price=intent.limit_price,
        stop_loss=_NOOP_STOP,
        take_profit=None,
        time_in_force="GTC",
        client_order_id=intent.client_order_id,
    )


async def _existing_order_id(client_order_id: str):
    factory = get_session_factory()
    async with factory() as session:
        return (await session.execute(
            select(Order.order_id).where(Order.client_order_id == client_order_id)
        )).scalar_one_or_none()


async def _record_intent_risk_event(
    *, strategy_id: int, client_order_id: str, reason: str,
) -> None:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(RiskEvent(
            event_id=uuid4(),
            kind=reason.split(" ")[0],
            severity="block",
            context={
                "strategy_id": strategy_id,
                "client_order_id": client_order_id,
                "reason": reason,
                "source": "strategy_intent",
            },
            action_taken="intent_dropped",
            at=now,
        ))
        await session.commit()


async def _persist_order_and_link(
    *, strategy_row: StrategyRow, intent: OrderIntent, request: OrderRequest,
    receipt, mode: str,
):
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    order_id = uuid4()
    async with factory() as session:
        session.add(Order(
            order_id=order_id,
            client_order_id=request.client_order_id,
            exchange_order_id=receipt.exchange_order_id,
            decision_id=None, signal_id=None, proposal_id=None,
            symbol=request.symbol, side=request.side,
            order_type=request.order_type,
            size_usd=request.size_usd,
            size_base=receipt.filled_base,
            limit_price=request.limit_price,
            stop_loss=request.stop_loss.model_dump(mode="json"),
            take_profit=None,
            status=receipt.status,
            execution_mode=mode,
            submitted_at=now,
            filled_at=now if receipt.status == "filled" else None,
            avg_fill_price=receipt.avg_fill_price,
            filled_base=receipt.filled_base,
            fees_usd=receipt.fees_usd,
            rejection_reason=receipt.rejection_reason,
            policy_version=_policy_version(strategy_row),
        ))
        await session.flush()
        # strategy_orders has no ORM model — raw insert (the strategies
        # layer uses raw SQL throughout; see strategies/repo.py).
        from sqlalchemy import text as _text
        await session.execute(
            _text(
                "INSERT INTO strategy_orders (strategy_id, order_id, role, grid_level) "
                "VALUES (:sid, :oid, :role, :gl)"
            ),
            {"sid": strategy_row.id, "oid": order_id,
             "role": intent.role, "gl": intent.grid_level},
        )
        await session.commit()
    return order_id


async def dispatch_intents(
    strategy_row: StrategyRow, intents: list[OrderIntent],
) -> int:
    """Submit each intent through the rail + adapter. Returns the number
    of orders actually placed. A rail-blocked or already-placed intent
    is skipped (no order); a block writes a risk_events row. Adapter
    errors propagate to the caller (the worker marks the strategy
    errored — an exchange/adapter failure is a real fault)."""
    if not intents:
        return 0

    from trading_sandwich.execution.policy_rails import evaluate_policy_for_intent
    from trading_sandwich.execution.worker import _adapter

    adapter, mode = _adapter()
    placed = 0
    for intent in intents:
        if await _existing_order_id(intent.client_order_id) is not None:
            continue  # idempotent re-emit (e.g. a still-resting grid order)

        request = intent_to_order_request(intent)
        block = await evaluate_policy_for_intent(request)
        if block:
            logger.warning(
                "strategy %d (%s on %s): intent %s blocked by rail: %s",
                strategy_row.id, strategy_row.strategy_type, strategy_row.symbol,
                intent.client_order_id, block,
            )
            await _record_intent_risk_event(
                strategy_id=strategy_row.id,
                client_order_id=intent.client_order_id,
                reason=block,
            )
            continue

        receipt = await adapter.submit_order(request)
        await _persist_order_and_link(
            strategy_row=strategy_row, intent=intent, request=request,
            receipt=receipt, mode=mode,
        )
        placed += 1
    return placed
