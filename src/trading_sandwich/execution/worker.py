"""submit_order Celery task. Loads paper or live adapter based on
policy.execution_mode and runs the 16-rail policy check before submitting.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update

from trading_sandwich import _policy
from trading_sandwich.celery_app import app
from trading_sandwich.contracts.phase2 import (
    OrderRequest,
    StopLossSpec,
    TakeProfitSpec,
)
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Order, TradeProposal


def _capture_policy_version() -> str:
    env = os.environ.get("TS_PROMPT_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/app",
        ).decode().strip()
    except Exception:
        return "unknown"


def _adapter():
    """Load the adapter dictated by policy.execution_mode at task start."""
    mode = _policy.get_execution_mode()
    if mode == "paper":
        from trading_sandwich.execution.adapters.paper import PaperAdapter
        return PaperAdapter(), "paper"
    if mode == "live":
        # Halal spot only — Spec A.5. CCXTProAdapter (margin) is kept in repo
        # for audit/historical but is no longer routed.
        from trading_sandwich.execution.adapters.ccxt_spot import CCXTSpotAdapter
        return CCXTSpotAdapter(), "live"
    raise ValueError(f"unknown execution_mode {mode!r}")


async def _load_proposal(proposal_id: UUID) -> TradeProposal | None:
    factory = get_session_factory()
    async with factory() as session:
        return (await session.execute(
            select(TradeProposal).where(TradeProposal.proposal_id == proposal_id)
        )).scalar_one_or_none()


async def _persist_order(
    proposal_id: UUID, request: OrderRequest, receipt, mode: str, policy_version: str,
) -> UUID:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    order_id = uuid4()
    async with factory() as session:
        session.add(Order(
            order_id=order_id,
            client_order_id=request.client_order_id,
            exchange_order_id=receipt.exchange_order_id,
            decision_id=None, signal_id=None,
            proposal_id=proposal_id,
            symbol=request.symbol, side=request.side,
            order_type=request.order_type,
            size_usd=request.size_usd,
            size_base=receipt.filled_base,
            limit_price=request.limit_price,
            stop_loss=request.stop_loss.model_dump(mode="json"),
            take_profit=(
                request.take_profit.model_dump(mode="json")
                if request.take_profit else None
            ),
            status=receipt.status,
            execution_mode=mode,
            submitted_at=now,
            filled_at=now if receipt.status == "filled" else None,
            avg_fill_price=receipt.avg_fill_price,
            filled_base=receipt.filled_base,
            fees_usd=receipt.fees_usd,
            rejection_reason=receipt.rejection_reason,
            policy_version=policy_version,
        ))
        await session.flush()
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .values(
                status=("executed" if receipt.status in ("filled", "open")
                        else "failed"),
                executed_order_id=order_id,
            )
        )
        await session.commit()
    return order_id


async def _submit_async(proposal_id: UUID) -> None:
    proposal = await _load_proposal(proposal_id)
    if proposal is None or proposal.status != "approved":
        return

    from trading_sandwich.execution.policy_rails import evaluate_policy
    block = await evaluate_policy(proposal)
    if block:
        from trading_sandwich.execution.policy_rails import record_risk_event
        await record_risk_event(proposal_id, block)
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(TradeProposal)
                .where(TradeProposal.proposal_id == proposal_id)
                .values(status="failed", rejected_at=datetime.now(timezone.utc))
            )
            await session.commit()
        return

    adapter, mode = _adapter()
    request = OrderRequest(
        symbol=proposal.symbol, side=proposal.side,
        order_type=proposal.order_type,
        size_usd=proposal.size_usd, limit_price=proposal.limit_price,
        stop_loss=StopLossSpec(**proposal.stop_loss),
        take_profit=(TakeProfitSpec(**proposal.take_profit)
                     if proposal.take_profit else None),
        time_in_force=proposal.time_in_force,
        client_order_id=proposal.proposal_id.hex,
    )
    # Discord: order submitted (Phase 2.7)
    from trading_sandwich.notifications.discord import (
        post_card_safe,
        render_order_filled_card,
        render_order_rejected_card,
        render_order_submitted_card,
    )
    now_submit = datetime.now(timezone.utc)
    await post_card_safe(render_order_submitted_card(
        occurred_at=now_submit,
        symbol=request.symbol,
        side=request.side,
        size_usd=float(request.size_usd),
        order_type=request.order_type,
        limit_price=float(request.limit_price) if request.limit_price else None,
    ))

    receipt = await adapter.submit_order(request)
    await _persist_order(proposal_id, request, receipt, mode, _capture_policy_version())

    # Discord: order outcome
    now_done = datetime.now(timezone.utc)
    if receipt.status == "filled":
        fill_price = float(receipt.avg_fill_price) if receipt.avg_fill_price else 0.0
        size_base = float(receipt.filled_base) if receipt.filled_base else 0.0
        notional = fill_price * size_base if fill_price and size_base else float(request.size_usd)
        await post_card_safe(render_order_filled_card(
            occurred_at=now_done,
            symbol=request.symbol,
            side=request.side,
            size_base=size_base,
            fill_price=fill_price,
            notional_usd=notional,
            fees_usd=float(receipt.fees_usd) if receipt.fees_usd else None,
        ))
    elif receipt.status in ("rejected", "failed"):
        await post_card_safe(render_order_rejected_card(
            occurred_at=now_done,
            symbol=request.symbol,
            side=request.side,
            size_usd=float(request.size_usd),
            reason=receipt.rejection_reason or "unknown",
        ))


@app.task(name="trading_sandwich.execution.worker.submit_order", acks_late=True)
def submit_order(proposal_id_str: str) -> None:
    asyncio.run(_submit_async(UUID(proposal_id_str)))
