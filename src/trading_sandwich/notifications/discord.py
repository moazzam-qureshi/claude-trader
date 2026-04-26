"""Discord webhook notifier for universe events."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx


WEBHOOK_ENV = "DISCORD_UNIVERSE_WEBHOOK_URL"


def _webhook_url() -> str:
    url = os.environ.get(WEBHOOK_ENV)
    if not url:
        raise RuntimeError(f"{WEBHOOK_ENV} not set")
    return url


def render_universe_event_card(
    *,
    occurred_at: datetime,
    event_type: str,
    symbol: str,
    from_tier: str | None,
    to_tier: str | None,
    rationale: str,
    reversion_criterion: str | None,
    shift_id: int | None,
    diary_ref: str | None,
) -> dict[str, Any]:
    movement = (
        f"{from_tier} → {to_tier}" if from_tier and to_tier
        else f"→ {to_tier}" if to_tier
        else f"from {from_tier}" if from_tier
        else ""
    )
    title_line = f"🔄 Universe change — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}"
    headline = f"**{symbol} {movement} ({event_type})**"
    parts = [title_line, headline, "", f"Rationale: {rationale}"]
    if reversion_criterion:
        parts.append(f"Reversion: {reversion_criterion}")
    meta = []
    if shift_id is not None:
        meta.append(f"shift_id: {shift_id}")
    if diary_ref:
        meta.append(f"diary: {diary_ref}")
    if meta:
        parts.append(" · ".join(meta))
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_hard_limit_blocked_card(
    *,
    occurred_at: datetime,
    attempted: dict[str, Any],
    blocked_by: str,
) -> dict[str, Any]:
    movement = (
        f"{attempted.get('from_tier', '?')} → {attempted.get('to_tier', '?')}"
        if attempted.get("from_tier") or attempted.get("to_tier")
        else ""
    )
    parts = [
        f"⛔ Hard limit blocked — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Claude attempted: **{attempted.get('event_type')} {attempted.get('symbol')}** {movement}",
        f"Blocked by: `{blocked_by}`",
        "",
        f"Rationale: {(attempted.get('rationale') or '')[:200]}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


# ---------------------------------------------------------------------------
# Trade lifecycle cards. All use the same DISCORD_UNIVERSE_WEBHOOK_URL so
# the operator gets one channel for every event worth seeing.
# ---------------------------------------------------------------------------


def render_proposal_card(
    *,
    occurred_at: datetime,
    proposal_id: str,
    symbol: str,
    side: str,
    size_usd: float,
    entry: float,
    stop: float,
    take_profit: float | None,
    rationale: str,
    expected_rr: float | None,
    auto_approve_in_seconds: int,
) -> dict[str, Any]:
    parts = [
        f"🟢 Trade proposal — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**{side.upper()} {symbol}** at ~${entry:.2f}  ·  size ${size_usd:.2f}",
        f"Stop: ${stop:.2f}  ·  TP: " + (f"${take_profit:.2f}" if take_profit else "—") +
        (f"  ·  RR: {expected_rr:.2f}" if expected_rr else ""),
        "",
        f"Rationale: {rationale[:400]}",
        "",
        f"⏱ Auto-approves in {auto_approve_in_seconds}s. proposal_id: `{proposal_id[:8]}…`",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_order_submitted_card(
    *,
    occurred_at: datetime,
    symbol: str,
    side: str,
    size_usd: float,
    order_type: str,
    limit_price: float | None,
) -> dict[str, Any]:
    parts = [
        f"🔵 Order submitted to Binance — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**{side.upper()} {symbol}** {order_type}  ·  size ${size_usd:.2f}",
    ]
    if limit_price:
        parts.append(f"Limit: ${limit_price:.2f}")
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_order_filled_card(
    *,
    occurred_at: datetime,
    symbol: str,
    side: str,
    size_base: float,
    fill_price: float,
    notional_usd: float,
    fees_usd: float | None = None,
) -> dict[str, Any]:
    fee_line = f"  ·  fees: ${fees_usd:.4f}" if fees_usd is not None else ""
    parts = [
        f"✅ Order filled — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**{side.upper()} {symbol}** filled at **${fill_price:.2f}**",
        f"Size: {size_base:.6f}  ·  notional ${notional_usd:.2f}{fee_line}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_order_rejected_card(
    *,
    occurred_at: datetime,
    symbol: str,
    side: str,
    size_usd: float,
    reason: str,
) -> dict[str, Any]:
    parts = [
        f"❌ Order rejected — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**{side.upper()} {symbol}** ${size_usd:.2f}",
        "",
        f"Reason: {reason[:500]}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_position_closed_card(
    *,
    occurred_at: datetime,
    symbol: str,
    side: str,
    entry: float,
    exit_price: float,
    realized_pnl_usd: float,
    pnl_pct: float,
    reason: str,
) -> dict[str, Any]:
    win = realized_pnl_usd > 0
    icon = "💰" if win else "🔻"
    parts = [
        f"{icon} Position closed — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**{side.upper()} {symbol}**  entry ${entry:.2f} → exit ${exit_price:.2f}",
        f"PnL: **${realized_pnl_usd:+.2f}** ({pnl_pct:+.2f}%)",
        "",
        f"Reason: {reason[:200]}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_proposal_expired_card(
    *,
    occurred_at: datetime,
    symbol: str,
    side: str,
    size_usd: float,
    expires_at: datetime,
) -> dict[str, Any]:
    parts = [
        f"⏰ Proposal expired — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**{side.upper()} {symbol}** ${size_usd:.2f}",
        f"TTL hit at {expires_at.strftime('%H:%M:%S UTC')} without operator action.",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_proposal_approved_card(
    *,
    occurred_at: datetime,
    symbol: str,
    side: str,
    size_usd: float,
    auto: bool,
) -> dict[str, Any]:
    by = "auto-approved" if auto else "operator-approved"
    parts = [
        f"👍 Proposal {by} — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**{side.upper()} {symbol}** ${size_usd:.2f}",
        "Routing to execution-worker.",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_risk_event_card(
    *,
    occurred_at: datetime,
    kind: str,
    severity: str,
    context: dict | str,
    action_taken: str | None,
) -> dict[str, Any]:
    icon = "⚠️" if severity == "warning" else ("🚨" if severity == "critical" else "ℹ️")
    ctx_str = str(context)[:300]
    parts = [
        f"{icon} Risk event ({severity}) — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Kind: `{kind}`",
        f"Action: {action_taken or '—'}",
        "",
        f"Context: {ctx_str}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_daily_summary_card(
    *,
    occurred_at: datetime,
    shifts: int,
    proposals: int,
    orders_filled: int,
    orders_rejected: int,
    universe_changes: int,
    open_positions: int,
    realized_pnl_usd: float,
    equity_usd: float,
) -> dict[str, Any]:
    pnl_color = "🟢" if realized_pnl_usd > 0 else ("🔻" if realized_pnl_usd < 0 else "⚪")
    parts = [
        f"📅 Daily summary — {occurred_at.strftime('%Y-%m-%d UTC')}",
        f"Shifts: **{shifts}**  ·  Proposals: **{proposals}**",
        f"Orders: **{orders_filled} filled** / {orders_rejected} rejected",
        f"Universe changes: {universe_changes}",
        f"Open positions: {open_positions}",
        "",
        f"{pnl_color} Realized PnL today: **${realized_pnl_usd:+.2f}**  ·  Equity: ${equity_usd:.2f}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_heartbeat_error_card(
    *,
    occurred_at: datetime,
    exit_reason: str,
    duration_seconds: int | None,
    stderr_excerpt: str,
) -> dict[str, Any]:
    parts = [
        f"⚠️ Heartbeat shift failed — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Exit: `{exit_reason}`  ·  Duration: {duration_seconds or '?'}s",
        "",
        f"stderr: ```{stderr_excerpt[:400]}```",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_shift_summary_card(
    *,
    occurred_at: datetime,
    shift_count: int,
    regime: str,
    open_positions: int,
    open_theses: int,
    next_check_in_minutes: int | None,
    duration_seconds: int | None,
    state_body_excerpt: str,
) -> dict[str, Any]:
    """Auto-posted card after every spawned shift completes. Not the trader's
    discretionary `notify_operator` channel — this is a system-level 'what
    happened in the latest shift' digest, fired by the heartbeat task itself.
    """
    next_label = (
        f"next ~{next_check_in_minutes}m" if next_check_in_minutes else "next ?m"
    )
    dur_label = f"{duration_seconds}s" if duration_seconds else "?s"
    parts = [
        f"⚙️ Shift #{shift_count} — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"regime: **{regime}**  ·  positions: {open_positions}  ·  "
        f"theses: {open_theses}  ·  ran in {dur_label}  ·  {next_label}",
        "",
        state_body_excerpt[:1200],
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_trader_note_card(
    *,
    occurred_at: datetime,
    severity: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    """Trader-authored Discord note. Severity drives the icon."""
    icon = {
        "info":      "💬",
        "watching":  "👀",
        "thinking":  "🧠",
        "concern":   "⚠️",
        "alert":     "🚨",
        "success":   "🎉",
    }.get(severity, "💬")
    parts = [
        f"{icon} **{title}** — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        body[:1500],
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_state_drift_card(
    *,
    occurred_at: datetime,
    state_says: int,
    db_says: int,
) -> dict[str, Any]:
    parts = [
        f"⚠️ STATE drift detected — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"STATE.md says open_positions={state_says}, DB says {db_says}.",
        "Trader will reconcile by trusting DB on the next shift.",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_kill_switch_card(
    *,
    occurred_at: datetime,
    active: bool,
    reason: str,
) -> dict[str, Any]:
    if active:
        parts = [
            f"🚨 KILL-SWITCH TRIPPED — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            f"Reason: {reason}",
            "",
            "All new orders are blocked. Open positions are NOT auto-closed (per policy).",
            "Resume with: `myapp trading resume --ack-reason \"...\"`",
        ]
    else:
        parts = [
            f"✅ Kill-switch RESUMED — {occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Operator ack: {reason}",
        ]
    return {"embeds": [{"description": "\n".join(parts)}]}


# ---------------------------------------------------------------------------


async def post_card(card: dict[str, Any]) -> str | None:
    """POST card to Discord webhook. Returns Discord message_id on success,
    None on failure."""
    url = _webhook_url()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{url}?wait=true", json=card)
            if resp.status_code >= 400:
                return None
            data = resp.json()
            return data.get("id")
    except Exception:
        return None


async def post_card_safe(card: dict[str, Any]) -> None:
    """Fire-and-forget Discord post. Never raises. For trade-lifecycle
    notifications where Discord failure must NEVER block the trade path.
    """
    try:
        if not os.environ.get(WEBHOOK_ENV):
            return  # webhook not configured — silently skip
        await post_card(card)
    except Exception:
        pass


async def retry_unposted_events(max_age_minutes: int = 1440) -> int:
    """Retry Discord posts for events with discord_posted=false.

    Returns count of events successfully posted.
    """
    from datetime import datetime, timedelta, timezone as _tz

    from sqlalchemy import select, text as _sql_text

    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_heartbeat import UniverseEvent

    cutoff = datetime.now(_tz.utc) - timedelta(minutes=max_age_minutes)
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(UniverseEvent).where(
                UniverseEvent.discord_posted.is_(False),
                UniverseEvent.occurred_at >= cutoff,
            )
        )).scalars().all()

    posted = 0
    for row in rows:
        if row.event_type == "hard_limit_blocked":
            card = render_hard_limit_blocked_card(
                occurred_at=row.occurred_at,
                attempted=row.attempted_change or {},
                blocked_by=row.blocked_by or "unknown",
            )
        else:
            card = render_universe_event_card(
                occurred_at=row.occurred_at,
                event_type=row.event_type,
                symbol=row.symbol,
                from_tier=row.from_tier,
                to_tier=row.to_tier,
                rationale=row.rationale,
                reversion_criterion=row.reversion_criterion,
                shift_id=row.shift_id,
                diary_ref=row.diary_ref,
            )
        msg_id = await post_card(card)
        if msg_id:
            async with factory() as session:
                await session.execute(_sql_text(
                    "UPDATE universe_events SET discord_posted=true, "
                    "discord_message_id=:m WHERE id=:i"
                ).bindparams(m=msg_id, i=row.id))
                await session.commit()
            posted += 1
    return posted
