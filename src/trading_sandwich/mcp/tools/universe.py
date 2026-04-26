"""MCP tools for universe state and curation: get_open_positions,
assess_symbol_fit, mutate_universe."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select, text

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import UniverseEvent
from trading_sandwich.db.models_phase2 import Position
from trading_sandwich.mcp.server import mcp
from trading_sandwich.notifications.discord import (
    post_card as _post_card,
    render_hard_limit_blocked_card,
    render_universe_event_card,
)
from trading_sandwich.triage.universe_policy import (
    HardLimitViolation,
    apply_mutation,
    load_universe,
    validate_mutation,
)


POLICY_PATH = Path(os.environ.get("TS_POLICY_PATH", "/app/policy.yaml"))


def _prompt_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd="/app"
        ).strip()
    except Exception:
        return "unknown"


@mcp.tool()
async def get_open_positions() -> list[dict]:
    """Return all currently open positions (closed_at IS NULL)."""
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(Position).where(Position.closed_at.is_(None))
        )).scalars().all()
        return [
            {
                "symbol": p.symbol,
                "side": p.side,
                "size_base": float(p.size_base),
                "avg_entry": float(p.avg_entry),
                "unrealized_pnl_usd": (
                    float(p.unrealized_pnl_usd)
                    if p.unrealized_pnl_usd is not None else None
                ),
                "opened_at": p.opened_at.isoformat(),
            }
            for p in rows
        ]


def _load_hard_limits() -> dict:
    raw = yaml.safe_load(POLICY_PATH.read_text())
    return raw["universe"]["hard_limits"]


@mcp.tool()
async def get_universe() -> dict:
    """Return the current tiered universe from policy.yaml.

    Use this at the start of every shift to see what symbols you are
    mandated to trade. Each tier carries: symbols, size_multiplier,
    max_concurrent_positions, shift_attention. Excluded symbols are
    listed for awareness but cannot be traded.
    """
    raw = yaml.safe_load(POLICY_PATH.read_text())
    universe = raw["universe"]
    return {
        "tiers": universe["tiers"],
        "hard_limits": universe["hard_limits"],
        "active_symbols": [
            sym
            for tier in ("core", "watchlist", "observation")
            for sym in universe["tiers"].get(tier, {}).get("symbols", [])
        ],
    }


async def _fetch_metrics(symbol: str) -> dict:
    """Stubbed in v1: returns zeros so untested symbols cleanly fail. Real
    impl in Spec B pulls from Binance + tradingview MCPs."""
    return {"volume_24h_usd": 0, "vol_30d_annualized": 0.0}


def assess_against_hard_limits(
    *,
    symbol: str,
    volume_24h_usd: float,
    vol_30d_annualized: float,
    hard_limits: dict,
) -> dict:
    structural = {"passes": True, "details": {"symbol": symbol}}
    liquidity_failed: list[str] = []
    if volume_24h_usd < hard_limits.get("min_24h_volume_usd_floor", 0):
        liquidity_failed.append("min_24h_volume_usd_floor")
    if vol_30d_annualized > hard_limits.get("vol_30d_annualized_max_ceiling", 1e9):
        liquidity_failed.append("vol_30d_annualized_max_ceiling")
    return {
        "structural": structural,
        "liquidity": {
            "passes": not liquidity_failed,
            "failed_criteria": liquidity_failed,
            "details": {
                "volume_24h_usd": volume_24h_usd,
                "vol_30d_annualized": vol_30d_annualized,
            },
        },
        "edge_evidence": {"passes": None, "reason": "deferred_to_spec_b"},
    }


@mcp.tool()
async def assess_symbol_fit(symbol: str) -> dict:
    """Check whether a symbol passes Layer 1 + Layer 2 hard-limit criteria."""
    metrics = await _fetch_metrics(symbol)
    hl = _load_hard_limits()
    res = assess_against_hard_limits(
        symbol=symbol,
        volume_24h_usd=metrics["volume_24h_usd"],
        vol_30d_annualized=metrics["vol_30d_annualized"],
        hard_limits=hl,
    )
    if res["liquidity"]["passes"] and res["structural"]["passes"]:
        res["recommendation"] = "observation_tier_eligible_pending_edge_evidence"
    else:
        res["recommendation"] = "rejected"
    return res


@mcp.tool()
async def mutate_universe(
    event_type: str,
    symbol: str,
    rationale: str,
    reversion_criterion: str | None = None,
    to_tier: str | None = None,
    shift_id: int | None = None,
) -> dict:
    """Mutate the universe (add/promote/demote/remove/exclude/unexclude).

    Validates against hard limits. On reject, records hard_limit_blocked
    event and posts Discord. On accept, atomically updates policy.yaml,
    records event, posts Discord.
    """
    req = UniverseMutationRequest(
        event_type=UniverseEventType(event_type),
        symbol=symbol,
        to_tier=to_tier,
        rationale=rationale,
        reversion_criterion=reversion_criterion,
    )
    policy = load_universe(POLICY_PATH)
    from_tier = policy.tier_of(symbol)
    occurred_at = datetime.now(timezone.utc)
    pv = _prompt_version()
    factory = get_session_factory()

    try:
        validate_mutation(policy, req)
    except HardLimitViolation as exc:
        async with factory() as session:
            row = UniverseEvent(
                occurred_at=occurred_at,
                shift_id=shift_id,
                event_type=UniverseEventType.HARD_LIMIT_BLOCKED.value,
                symbol=symbol,
                rationale=rationale,
                attempted_change={
                    "event_type": event_type,
                    "symbol": symbol,
                    "from_tier": from_tier,
                    "to_tier": to_tier,
                    "rationale": rationale,
                    "reversion_criterion": reversion_criterion,
                },
                blocked_by=exc.limit,
                prompt_version=pv,
            )
            session.add(row)
            await session.commit()
            event_id = row.id
        card = render_hard_limit_blocked_card(
            occurred_at=occurred_at,
            attempted={
                "event_type": event_type, "symbol": symbol,
                "from_tier": from_tier, "to_tier": to_tier,
                "rationale": rationale,
            },
            blocked_by=exc.limit,
        )
        msg_id = await _post_card(card)
        if msg_id:
            async with factory() as session:
                await session.execute(text(
                    "UPDATE universe_events SET discord_posted=true, "
                    "discord_message_id=:m WHERE id=:i"
                ).bindparams(m=msg_id, i=event_id))
                await session.commit()
        return {"accepted": False, "blocked_by": exc.limit, "event_id": event_id}

    apply_mutation(POLICY_PATH, policy, req)
    async with factory() as session:
        row = UniverseEvent(
            occurred_at=occurred_at,
            shift_id=shift_id,
            event_type=event_type,
            symbol=symbol,
            from_tier=from_tier,
            to_tier=to_tier,
            rationale=rationale,
            reversion_criterion=reversion_criterion,
            prompt_version=pv,
        )
        session.add(row)
        await session.commit()
        event_id = row.id

    card = render_universe_event_card(
        occurred_at=occurred_at,
        event_type=event_type,
        symbol=symbol,
        from_tier=from_tier,
        to_tier=to_tier,
        rationale=rationale,
        reversion_criterion=reversion_criterion,
        shift_id=shift_id,
        diary_ref=None,
    )
    msg_id = await _post_card(card)
    if msg_id:
        async with factory() as session:
            await session.execute(text(
                "UPDATE universe_events SET discord_posted=true, "
                "discord_message_id=:m WHERE id=:i"
            ).bindparams(m=msg_id, i=event_id))
            await session.commit()
    return {"accepted": True, "event_id": event_id}
