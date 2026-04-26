"""propose_trade MCP tool.

Cross-checks before persisting:
1. expected_rr >= policy.default_rr_minimum.
2. worst_case_loss_usd ≈ size_usd × |entry-stop| / entry (within 2% tolerance).
3. decision exists and decision.decision == 'paper_trade'.
4. similar_signals_count matches a fresh find_similar_signals(k=100).
5. stop distance within policy ATR band.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select

from trading_sandwich import _policy
from trading_sandwich.config import get_settings
from trading_sandwich.contracts.phase2 import StopLossSpec, TakeProfitSpec
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.db.models_phase2 import TradeProposal
from trading_sandwich.discord.embed import render_proposal_embed
from trading_sandwich.discord.webhook import post_webhook
from trading_sandwich.mcp.server import mcp


async def _count_similar_signals(signal_id: UUID) -> int:
    from trading_sandwich.mcp.tools.reads import find_similar_signals
    result = await find_similar_signals(signal_id, k=100)
    return len(result.results)


def _capture_policy_version() -> str:
    env = os.environ.get("TS_PROMPT_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/workspace"
        ).decode().strip()
    except Exception:
        return "unknown"


@mcp.tool()
async def propose_trade(
    decision_id: UUID,
    symbol: str,
    side: Literal["long", "short"],
    order_type: Literal["market", "limit", "stop"],
    size_usd: Decimal,
    limit_price: Decimal | None,
    stop_loss: StopLossSpec,
    take_profit: TakeProfitSpec | None,
    opportunity: str,
    risk: str,
    profit_case: str,
    alignment: str,
    similar_trades_evidence: str,
    expected_rr: Decimal,
    worst_case_loss_usd: Decimal,
    similar_signals_count: int,
    similar_signals_win_rate: Decimal | None = None,
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC",
) -> UUID:
    """Propose a trade. Persisted only if all cross-checks pass."""
    rr_min = _policy.get_default_rr_minimum()
    if expected_rr < rr_min:
        raise ValueError(f"expected_rr {expected_rr} < default_rr_minimum {rr_min}")

    entry = limit_price if limit_price is not None else Decimal("0")
    if entry == 0 and order_type == "market":
        if worst_case_loss_usd <= 0:
            raise ValueError("worst_case_loss_usd must be > 0")
    else:
        stop = stop_loss.value
        computed = (size_usd * abs(entry - stop) / entry).quantize(Decimal("0.01"))
        tol = computed * Decimal("0.02") + Decimal("0.01")
        if abs(worst_case_loss_usd - computed) > tol:
            raise ValueError(
                f"worst_case_loss_usd {worst_case_loss_usd} != computed {computed} (tol {tol})"
            )

    factory = get_session_factory()
    async with factory() as session:
        decision = (await session.execute(
            select(ClaudeDecision).where(ClaudeDecision.decision_id == decision_id)
        )).scalar_one_or_none()
        if decision is None:
            raise ValueError(f"decision_id {decision_id} not found")
        if decision.decision != "paper_trade":
            raise ValueError(
                f"propose_trade requires decision='paper_trade', got {decision.decision!r}"
            )
        signal_id = decision.signal_id
        if signal_id is None:
            raise ValueError(f"decision {decision_id} has null signal_id")

        signal = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == signal_id)
        )).scalar_one()

    actual_count = await _count_similar_signals(signal_id)
    if abs(actual_count - similar_signals_count) > 2:
        raise ValueError(
            f"similar_signals_count {similar_signals_count} disagrees with "
            f"actual {actual_count}"
        )

    atr = signal.features_snapshot.get("atr_14")
    if atr:
        atr_d = Decimal(str(atr))
        price = limit_price or signal.trigger_price
        dist_atr = abs(price - stop_loss.value) / atr_d
        if dist_atr < _policy.get_min_stop_distance_atr() or dist_atr > _policy.get_max_stop_distance_atr():
            raise ValueError(
                f"stop distance {dist_atr}·ATR outside band "
                f"[{_policy.get_min_stop_distance_atr()}, {_policy.get_max_stop_distance_atr()}]"
            )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=_policy.get_proposal_ttl_minutes())
    proposal_id = uuid4()
    async with factory() as session:
        session.add(TradeProposal(
            proposal_id=proposal_id,
            decision_id=decision_id,
            signal_id=signal_id,
            symbol=symbol, side=side, order_type=order_type,
            size_usd=size_usd, limit_price=limit_price,
            stop_loss=stop_loss.model_dump(mode="json"),
            take_profit=take_profit.model_dump(mode="json") if take_profit else None,
            time_in_force=time_in_force,
            opportunity=opportunity, risk=risk, profit_case=profit_case,
            alignment=alignment, similar_trades_evidence=similar_trades_evidence,
            expected_rr=expected_rr, worst_case_loss_usd=worst_case_loss_usd,
            similar_signals_count=similar_signals_count,
            similar_signals_win_rate=similar_signals_win_rate,
            status="pending",
            proposed_at=now, expires_at=expires,
            policy_version=_capture_policy_version(),
        ))
        await session.commit()

    settings = get_settings()
    if settings.discord_webhook_url:
        embed = render_proposal_embed(
            proposal_id=proposal_id,
            symbol=symbol, side=side, archetype=signal.archetype,
            timeframe=signal.timeframe,
            size_usd=size_usd,
            entry=limit_price or signal.trigger_price,
            stop=stop_loss.value,
            stop_atr_mult=(
                Decimal(str(stop_loss.value))
                if stop_loss.kind == "atr_multiple" else Decimal("0")
            ),
            tp=take_profit.value if take_profit else None,
            expected_rr=expected_rr,
            worst_case_loss_usd=worst_case_loss_usd,
            worst_case_pct_equity=(worst_case_loss_usd / Decimal("500") * 100).quantize(Decimal("0.01")),
            similar_count=similar_signals_count,
            similar_win_rate=similar_signals_win_rate,
            similar_median_r="+0.0R",
            opportunity=opportunity, risk=risk, profit_case=profit_case,
            alignment=alignment,
            similar_trades_evidence=similar_trades_evidence,
            expires_at=expires,
        )
        try:
            await post_webhook(
                settings.discord_webhook_url,
                {"embeds": [embed], "components": embed.get("components", [])},
            )
        except Exception:
            pass

    # Phase 2.7 — also post to the universe-events channel so heartbeat-trader
    # operators see proposals in the same feed as universe mutations. Same
    # webhook URL the heartbeat trader uses for universe notifications.
    from trading_sandwich.notifications.discord import (
        post_card_safe,
        render_proposal_card,
    )
    auto_approve_seconds = int(os.environ.get("AUTO_APPROVE_AFTER_SECONDS", "60"))
    rationale = (opportunity or "")[:400]
    await post_card_safe(render_proposal_card(
        occurred_at=now,
        proposal_id=str(proposal_id),
        symbol=symbol,
        side=side,
        size_usd=float(size_usd),
        entry=float(limit_price or signal.trigger_price),
        stop=float(stop_loss.value),
        take_profit=float(take_profit.value) if take_profit else None,
        rationale=rationale,
        expected_rr=float(expected_rr) if expected_rr else None,
        auto_approve_in_seconds=auto_approve_seconds,
    ))

    return proposal_id
