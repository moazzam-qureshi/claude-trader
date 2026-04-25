"""16-rail pre-trade policy check.

Twelve rails inherited from Phase 0 spec §5 Stage 6, four new in Phase 2.
Run in order; first non-None return short-circuits with that block reason.

The kill-switch is BOTH the first rail and a persisted state row that
survives worker restart (see kill_switch.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from trading_sandwich import _policy
from trading_sandwich.config import get_settings
from trading_sandwich.contracts.phase2 import AccountState
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import (
    KillSwitchState,
    Order,
    Position,
    RiskEvent,
)


# --- helpers ----------------------------------------------------------------

async def _kill_switch_active() -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(KillSwitchState).where(KillSwitchState.id == 1)
        )).scalar_one_or_none()
        return bool(row.active) if row else False


async def _account_state() -> AccountState:
    """Load adapter-reported account state. In paper mode this is synthesized."""
    from trading_sandwich.execution.worker import _adapter
    adapter, _ = _adapter()
    return await adapter.get_account_state()


async def _executed_today_count() -> int:
    factory = get_session_factory()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with factory() as session:
        n = (await session.execute(
            select(func.count(Order.order_id)).where(Order.submitted_at >= today)
        )).scalar_one()
        return int(n)


async def _open_positions_for_symbol(symbol: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        n = (await session.execute(
            select(func.count())
            .select_from(Position)
            .where(Position.symbol == symbol, Position.closed_at.is_(None))
        )).scalar_one()
        return int(n)


async def _open_positions_total() -> int:
    factory = get_session_factory()
    async with factory() as session:
        n = (await session.execute(
            select(func.count()).select_from(Position).where(Position.closed_at.is_(None))
        )).scalar_one()
        return int(n)


async def _correlated_total_usd() -> Decimal:
    factory = get_session_factory()
    async with factory() as session:
        total = (await session.execute(
            select(func.coalesce(func.sum(Position.size_base * Position.avg_entry), 0))
            .where(Position.closed_at.is_(None))
        )).scalar_one()
    return Decimal(str(total))


# --- rails ------------------------------------------------------------------


async def rail_kill_switch(proposal, account: AccountState) -> str | None:
    if await _kill_switch_active():
        return "kill_switch_active"
    return None


async def rail_trading_enabled(proposal, account: AccountState) -> str | None:
    if not _policy.is_trading_enabled():
        return "trading_disabled"
    return None


async def rail_max_order_usd(proposal, account: AccountState) -> str | None:
    cap = _policy.get_max_order_usd()
    if Decimal(str(proposal.size_usd)) > cap:
        return f"max_order_usd_exceeded ({proposal.size_usd} > {cap})"
    return None


async def rail_max_open_positions_per_symbol(proposal, account: AccountState) -> str | None:
    cap = int(_policy.load_policy()["max_open_positions_per_symbol"])
    if await _open_positions_for_symbol(proposal.symbol) >= cap:
        return "max_open_positions_per_symbol_exceeded"
    return None


async def rail_max_open_positions_total(proposal, account: AccountState) -> str | None:
    cap = int(_policy.load_policy()["max_open_positions_total"])
    if await _open_positions_total() >= cap:
        return "max_open_positions_total_exceeded"
    return None


async def rail_max_daily_realized_loss(proposal, account: AccountState) -> str | None:
    cap = Decimal(str(_policy.load_policy()["max_daily_realized_loss_usd"]))
    if account.realized_pnl_today_usd < -cap:
        return "max_daily_realized_loss_breached"
    return None


async def rail_max_orders_per_day(proposal, account: AccountState) -> str | None:
    cap = int(_policy.load_policy()["max_orders_per_day"])
    if await _executed_today_count() >= cap:
        return "max_orders_per_day_exceeded"
    return None


async def rail_per_symbol_cooldown_after_loss(proposal, account: AccountState) -> str | None:
    # MVP: skip this rail for Phase 2; revisit in Phase 3 with a real
    # per-symbol last-loss timestamp lookup. Returning None = allow.
    return None


async def rail_stop_loss_required(proposal, account: AccountState) -> str | None:
    if proposal.stop_loss is None:
        return "stop_loss_required"
    return None


async def rail_stop_loss_sanity_band(proposal, account: AccountState) -> str | None:
    # Skipped in pre-trade; the propose_trade tool already enforces this band.
    return None


async def rail_max_leverage(proposal, account: AccountState) -> str | None:
    cap = Decimal(str(_policy.load_policy()["max_leverage"]))
    if account.leverage_used > cap:
        return f"max_leverage_exceeded ({account.leverage_used} > {cap})"
    return None


async def rail_correlated_exposure(proposal, account: AccountState) -> str | None:
    cap = Decimal(str(_policy.load_policy()["max_correlated_usd"]))
    total = await _correlated_total_usd()
    if total + Decimal(str(proposal.size_usd)) > cap:
        return "max_correlated_usd_exceeded"
    return None


async def rail_universe_allowlist(proposal, account: AccountState) -> str | None:
    if proposal.symbol not in _policy.get_universe_symbols():
        return f"symbol_not_in_universe ({proposal.symbol})"
    return None


# --- new Phase 2 rails ------------------------------------------------------


async def rail_first_trade_of_day_cap(proposal, account: AccountState) -> str | None:
    if await _executed_today_count() > 0:
        return None
    cap = _policy.get_max_order_usd() * _policy.get_first_trade_size_multiplier()
    if Decimal(str(proposal.size_usd)) > cap:
        return f"first_trade_size_cap ({proposal.size_usd} > {cap})"
    return None


async def rail_execution_mode_gating(proposal, account: AccountState) -> str | None:
    if _policy.get_execution_mode() == "live":
        s = get_settings()
        if not getattr(s, "binance_api_key", None):
            return "live_mode_without_api_key"
    return None


async def rail_stopless_runtime_assert(proposal, account: AccountState) -> str | None:
    if proposal.stop_loss is None:
        return "stopless_runtime_assert"
    return None


async def rail_account_state_sanity(proposal, account: AccountState) -> str | None:
    required = Decimal(str(proposal.size_usd)) * Decimal("1.2")
    if account.free_margin_usd < required:
        return f"insufficient_free_margin ({account.free_margin_usd} < {required})"
    return None


# --- dispatcher -------------------------------------------------------------


_RAILS_IN_ORDER = [
    rail_kill_switch,
    rail_trading_enabled,
    rail_max_order_usd,
    rail_max_open_positions_per_symbol,
    rail_max_open_positions_total,
    rail_max_daily_realized_loss,
    rail_max_orders_per_day,
    rail_per_symbol_cooldown_after_loss,
    rail_stop_loss_required,
    rail_stop_loss_sanity_band,
    rail_max_leverage,
    rail_correlated_exposure,
    rail_universe_allowlist,
    rail_first_trade_of_day_cap,
    rail_execution_mode_gating,
    rail_stopless_runtime_assert,
    rail_account_state_sanity,
]


async def evaluate_policy(proposal) -> str | None:
    """Run all rails in order. Returns the first block reason or None."""
    account = await _account_state()
    for rail in _RAILS_IN_ORDER:
        block = await rail(proposal, account)
        if block:
            return block
    return None


async def record_risk_event(proposal_id: UUID, reason: str, severity: str = "block") -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(RiskEvent(
            event_id=uuid4(), kind=reason.split(" ")[0], severity=severity,
            context={"proposal_id": str(proposal_id), "reason": reason},
            action_taken="proposal_failed", at=datetime.now(timezone.utc),
        ))
        await session.commit()
