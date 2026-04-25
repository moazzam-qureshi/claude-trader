"""Transactional proposal state transitions triggered by Discord interactions."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import TradeProposal


class ProposalExpired(Exception):
    pass


class ProposalNotPending(Exception):
    pass


class ProposalNotFound(Exception):
    pass


def _enqueue_submit_order(proposal_id: UUID) -> None:
    """Enqueues submit_order on the execution queue. Wired to execution-worker
    in Stage 1b plan; for Stage 1a this is a stub that can be patched in tests.
    """
    try:
        from trading_sandwich.execution.worker import submit_order
        submit_order.delay(str(proposal_id))
    except ImportError:
        pass


async def approve_proposal(proposal_id: UUID, approver: str) -> None:
    """FOR UPDATE → verify status pending and not expired → flip status → enqueue."""
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        row = (await session.execute(
            select(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise ProposalNotFound(str(proposal_id))
        if row.status != "pending":
            raise ProposalNotPending(f"{proposal_id} status={row.status}")
        if row.expires_at < now:
            await session.execute(
                update(TradeProposal)
                .where(TradeProposal.proposal_id == proposal_id)
                .values(status="expired", rejected_at=now)
            )
            await session.commit()
            raise ProposalExpired(str(proposal_id))
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .values(status="approved", approved_at=now, approved_by=approver)
        )
        await session.commit()
    _enqueue_submit_order(proposal_id)


async def reject_proposal(proposal_id: UUID) -> None:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        row = (await session.execute(
            select(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise ProposalNotFound(str(proposal_id))
        if row.status != "pending":
            raise ProposalNotPending(f"{proposal_id} status={row.status}")
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .values(status="rejected", rejected_at=now)
        )
        await session.commit()


async def handle_approve(interaction, proposal_id: UUID) -> None:
    try:
        await approve_proposal(proposal_id, approver=str(interaction.user.id))
        await interaction.response.edit_message(content="✅ Approved, submitting…", view=None)
    except ProposalExpired:
        await interaction.response.edit_message(content="⏰ Expired", view=None)
    except ProposalNotPending as exc:
        await interaction.response.send_message(f"not pending: {exc}", ephemeral=True)


async def handle_reject(interaction, proposal_id: UUID) -> None:
    try:
        await reject_proposal(proposal_id)
        await interaction.response.edit_message(content="❌ Rejected", view=None)
    except ProposalNotPending as exc:
        await interaction.response.send_message(f"not pending: {exc}", ephemeral=True)


async def handle_details(interaction, proposal_id: UUID) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(TradeProposal).where(TradeProposal.proposal_id == proposal_id)
        )).scalar_one_or_none()
    if row is None:
        body = "(proposal not found)"
    else:
        body = (
            f"```\nproposal_id: {row.proposal_id}\nsymbol: {row.symbol}\n"
            f"side: {row.side}\nsize_usd: {row.size_usd}\nstatus: {row.status}\n```"
        )
    await interaction.response.send_message(body[:1900], ephemeral=True)
