"""Live dashboard for the heartbeat trader (Phase 2.7+).

Polls Postgres + filesystem every few seconds and shows in priority order:
  1. Trader state (mode, equity, last shift, next check, positions)
  2. Live STATE.md body — what the trader is currently watching
  3. Universe by tier
  4. Recent shifts
  5. Recent universe events
  6. Open proposals + recent orders
  7. Kill-switch state

Replaces watch_decisions.py which was designed for the older signal-driven
triage path. Run it inside the tools container:

    docker compose run --rm tools python //app/scripts/watch_trader.py

Add --interval N to change refresh cadence (default 5s); --once for a
single snapshot.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift, UniverseEvent
from trading_sandwich.db.models_phase2 import (
    KillSwitchState,
    Order,
    Position,
    TradeProposal,
)


# ---------------- ANSI ------------------------------------------------------

CSI = "\x1b["
C_RESET = f"{CSI}0m"
C_BOLD = f"{CSI}1m"
C_DIM = f"{CSI}2m"
C_RED = f"{CSI}38;5;203m"
C_GREEN = f"{CSI}38;5;120m"
C_YELLOW = f"{CSI}38;5;221m"
C_BLUE = f"{CSI}38;5;111m"
C_MAGENTA = f"{CSI}38;5;177m"
C_CYAN = f"{CSI}38;5;87m"
C_GRAY = f"{CSI}38;5;245m"
C_DARK = f"{CSI}38;5;240m"


def color(s: str, c: str) -> str:
    return f"{c}{s}{C_RESET}"


def clear_screen() -> None:
    sys.stdout.write(f"{CSI}H{CSI}2J")
    sys.stdout.flush()


def term_width(default: int = 120) -> int:
    try:
        return shutil.get_terminal_size().columns
    except OSError:
        return default


def relative_time(ts: datetime) -> str:
    delta = (datetime.now(timezone.utc) - ts).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        h = int(delta // 3600)
        m = int((delta % 3600) // 60)
        return f"{h}h {m}m ago"
    d = int(delta // 86400)
    h = int((delta % 86400) // 3600)
    return f"{d}d {h}h ago"


def truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = s.replace("\r", "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


# ---------------- Panel helpers --------------------------------------------

def panel(title: str, body_lines: list[str], width: int, title_color: str = C_BOLD) -> list[str]:
    """Render a labeled box. Body lines are pre-rendered (may include ANSI)."""
    inner = max(20, width - 4)
    out = []
    title_str = f" {title} "
    bar_len = max(0, inner - len(title_str))
    out.append(color(f"┌─{title_str}{'─' * bar_len}┐", C_DARK))
    for line in body_lines:
        # We don't pad to width — easier to read variable-length content.
        out.append(color("│ ", C_DARK) + line)
    out.append(color(f"└{'─' * (inner + 2)}┘", C_DARK))
    return out


# ---------------- Fetchers --------------------------------------------------

POLICY_PATH = Path("/app/policy.yaml")
STATE_PATH = Path("/app/runtime/STATE.md")
DIARY_DIR = Path("/app/runtime/diary")


def _load_policy() -> dict:
    try:
        return yaml.safe_load(POLICY_PATH.read_text())
    except Exception:
        return {}


def _load_state_md() -> tuple[dict, str]:
    """Return (frontmatter dict, body markdown). Empty on failure."""
    try:
        import frontmatter
        post = frontmatter.load(str(STATE_PATH))
        return post.metadata, post.content
    except Exception:
        return {}, ""


async def _last_shift(session) -> HeartbeatShift | None:
    return (await session.execute(
        select(HeartbeatShift)
        .order_by(HeartbeatShift.started_at.desc())
        .limit(1)
    )).scalars().first()


async def _last_spawned_shift(session) -> HeartbeatShift | None:
    return (await session.execute(
        select(HeartbeatShift)
        .where(HeartbeatShift.spawned.is_(True))
        .order_by(HeartbeatShift.started_at.desc())
        .limit(1)
    )).scalars().first()


async def _shift_counts(session) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    today = (await session.execute(
        select(func.count(HeartbeatShift.id)).where(
            HeartbeatShift.spawned.is_(True),
            HeartbeatShift.started_at >= today_start,
        )
    )).scalar_one()
    week = (await session.execute(
        select(func.count(HeartbeatShift.id)).where(
            HeartbeatShift.spawned.is_(True),
            HeartbeatShift.started_at >= week_start,
        )
    )).scalar_one()
    return today, week


async def _open_positions(session) -> list[Position]:
    return list((await session.execute(
        select(Position).where(Position.closed_at.is_(None))
        .order_by(Position.opened_at.desc())
    )).scalars().all())


async def _recent_shifts(session, limit: int = 8) -> list[HeartbeatShift]:
    return list((await session.execute(
        select(HeartbeatShift)
        .order_by(HeartbeatShift.started_at.desc())
        .limit(limit)
    )).scalars().all())


async def _recent_universe_events(session, limit: int = 6) -> list[UniverseEvent]:
    return list((await session.execute(
        select(UniverseEvent)
        .order_by(UniverseEvent.occurred_at.desc())
        .limit(limit)
    )).scalars().all())


async def _open_proposals(session, limit: int = 5) -> list[TradeProposal]:
    return list((await session.execute(
        select(TradeProposal)
        .where(TradeProposal.status.in_(("pending", "approved")))
        .order_by(TradeProposal.proposed_at.desc())
        .limit(limit)
    )).scalars().all())


async def _recent_orders(session, limit: int = 5) -> list[Order]:
    return list((await session.execute(
        select(Order)
        .order_by(Order.submitted_at.desc())
        .limit(limit)
    )).scalars().all())


async def _kill_switch(session) -> dict:
    row = (await session.execute(
        select(KillSwitchState).where(KillSwitchState.id == 1)
    )).scalar_one_or_none()
    if row is None:
        return {"active": False, "reason": None}
    return {"active": bool(row.active), "reason": row.tripped_reason}


# ---------------- Section renderers ----------------------------------------

def render_trader_state(
    *, policy: dict, last: HeartbeatShift | None,
    last_spawned: HeartbeatShift | None,
    today_count: int, week_count: int,
    positions: list[Position],
    state_fm: dict,
    ks: dict,
    width: int,
) -> list[str]:
    mode = policy.get("execution_mode", "?")
    trading_enabled = policy.get("trading_enabled", False)
    max_lev = policy.get("max_leverage", "?")
    max_order = policy.get("max_order_usd", "?")

    mode_color = (
        C_RED if mode == "live" and trading_enabled else
        C_YELLOW if mode == "live" else
        C_BLUE if mode == "paper" else C_GRAY
    )
    mode_label = "LIVE" if mode == "live" else "PAPER" if mode == "paper" else mode
    if not trading_enabled:
        mode_label += " (trading_enabled=false)"

    if ks["active"]:
        ks_label = color(f"⏻ KILL-SWITCH ACTIVE: {ks['reason'] or '?'}", C_RED + C_BOLD)
    else:
        ks_label = color("⏻ kill-switch inactive", C_DARK)

    last_line = "  (no shifts yet)"
    if last_spawned is not None:
        rel = relative_time(last_spawned.started_at)
        nci = last_spawned.next_check_in_minutes
        # Estimate next eligible shift time
        next_at = last_spawned.started_at + timedelta(minutes=nci or 60)
        until_next = (next_at - datetime.now(timezone.utc)).total_seconds()
        until_label = (
            f"in {int(until_next // 60)}m" if until_next > 0
            else f"due now ({int(-until_next // 60)}m past)"
        )
        next_color = C_GREEN if until_next < 0 else C_GRAY
        last_line = (
            f"  Last spawned shift: {color(last_spawned.started_at.strftime('%m-%d %H:%M:%S UTC'), C_CYAN)} "
            f"({rel})  ·  next eligible: {color(until_label, next_color)}"
        )

    open_pos_count = len(positions)
    pos_label = (
        color(f"{open_pos_count} open", C_GREEN if open_pos_count > 0 else C_DARK)
    )
    theses = state_fm.get("open_theses", "?")
    regime = state_fm.get("regime", "?")
    pacing = state_fm.get("next_check_in_minutes", "?")

    body = [
        f"  {color('Mode:', C_GRAY)} {color(mode_label, mode_color + C_BOLD)}"
        f"   {color('max_leverage:', C_GRAY)} {max_lev}"
        f"   {color('max_order_usd:', C_GRAY)} ${max_order}"
        f"   {ks_label}",
        last_line,
        f"  {color('Shifts today:', C_GRAY)} {today_count}"
        f"   {color('this week:', C_GRAY)} {week_count}"
        f"   {color('Positions:', C_GRAY)} {pos_label}"
        f"   {color('Active theses:', C_GRAY)} {theses}"
        f"   {color('Regime:', C_GRAY)} {color(str(regime), C_CYAN)}"
        f"   {color('Trader pacing:', C_GRAY)} {pacing}min",
    ]
    return panel("TRADER STATE", body, width)


def render_what_watching(state_body: str, last_spawned_at: datetime | None, width: int) -> list[str]:
    """Render the live STATE.md body — the trader's working memory."""
    if not state_body.strip():
        return panel("WHAT THE TRADER IS WATCHING (live STATE.md)", [
            color("  (STATE.md is empty)", C_DIM)
        ], width)

    inner = max(40, width - 6)
    body_lines: list[str] = []
    age_label = (
        f"  {color('(state written ' + relative_time(last_spawned_at) + ')', C_DARK)}"
        if last_spawned_at is not None else ""
    )
    if age_label:
        body_lines.append(age_label)
        body_lines.append("")

    for raw in state_body.splitlines():
        if not raw.strip():
            body_lines.append("")
            continue
        # Color section headers (## Foo)
        if raw.lstrip().startswith("##"):
            body_lines.append(f"  {color(raw, C_MAGENTA + C_BOLD)}")
            continue
        # Highlight bullets
        if raw.lstrip().startswith(("-", "*")):
            body_lines.append(f"  {color(raw, C_GRAY)}")
            continue
        body_lines.append(f"  {color(truncate(raw, inner), C_RESET)}")
    return panel("WHAT THE TRADER IS WATCHING (live STATE.md)", body_lines, width)


def render_universe(policy: dict, width: int) -> list[str]:
    universe = (policy.get("universe") or {}).get("tiers", {})
    body = []
    for tier in ("core", "watchlist", "observation", "excluded"):
        symbols = universe.get(tier, {}).get("symbols", [])
        n = len(symbols)
        sym_str = ", ".join(symbols) if symbols else "(empty)"
        tier_color = {
            "core": C_GREEN, "watchlist": C_CYAN,
            "observation": C_BLUE, "excluded": C_DARK,
        }[tier]
        size_mult = universe.get(tier, {}).get("size_multiplier")
        smult = f" size×{size_mult}" if size_mult is not None else ""
        body.append(
            f"  {color(f'{tier:<12}', tier_color + C_BOLD)} "
            f"({n:>2}){color(smult, C_DARK)}: {sym_str}"
        )
    return panel("UNIVERSE", body, width)


def render_open_positions(positions: list[Position], width: int) -> list[str]:
    if not positions:
        body = [color("  (no open positions)", C_DIM)]
        return panel("OPEN POSITIONS", body, width)
    body = [
        color(
            f"  {'symbol':<10}  {'side':<5}  {'size_base':>12}  {'entry':>10}  {'opened'}",
            C_GRAY,
        ),
    ]
    for p in positions:
        body.append(
            f"  {color(p.symbol, C_CYAN):<10}  "
            f"{color(p.side, C_GREEN if p.side == 'long' else C_RED):<5}  "
            f"{float(p.size_base):>12.6f}  "
            f"{float(p.avg_entry):>10.2f}  "
            f"{relative_time(p.opened_at)}"
        )
    return panel("OPEN POSITIONS", body, width)


def render_recent_shifts(rows: list[HeartbeatShift], width: int) -> list[str]:
    if not rows:
        return panel("RECENT SHIFTS", [color("  (no shifts yet)", C_DIM)], width)
    body = [
        color(
            f"  {'when':<22}  {'spawned':<8}  {'exit':<13}  "
            f"{'wait':>5}  {'next':>5}  reason",
            C_GRAY,
        ),
    ]
    inner = max(40, width - 80)
    for r in rows:
        ts = r.started_at.strftime("%m-%d %H:%M:%S")
        rel = relative_time(r.started_at)
        if r.spawned:
            spawn_label = color("✓ yes", C_GREEN)
        else:
            spawn_label = color("· no ", C_DARK)
        exit_label = (r.exit_reason or "—")[:13]
        if r.exit_reason == "completed":
            exit_label = color(f"{exit_label:<13}", C_GREEN)
        elif r.exit_reason in ("timeout", "error"):
            exit_label = color(f"{exit_label:<13}", C_RED)
        else:
            exit_label = color(f"{exit_label:<13}", C_DARK)
        wait = r.actual_interval_min
        nci = r.next_check_in_minutes
        reason = truncate(r.next_check_reason, inner)
        body.append(
            f"  {color(ts, C_CYAN):<22}  {spawn_label:<8}  {exit_label}  "
            f"{(str(wait) if wait is not None else '—'):>5}  "
            f"{(str(nci) if nci is not None else '—'):>5}  "
            f"{color(reason, C_GRAY)}"
        )
        body.append(f"  {color('   ' + rel, C_DARK)}")
    return panel("RECENT SHIFTS", body, width)


def render_universe_events(rows: list[UniverseEvent], width: int) -> list[str]:
    if not rows:
        return panel("UNIVERSE EVENTS", [color("  (no events yet)", C_DIM)], width)
    inner = max(40, width - 80)
    body = [
        color(
            f"  {'when':<22}  {'event':<22}  {'symbol':<10}  "
            f"{'transition':<22}  rationale",
            C_GRAY,
        ),
    ]
    for r in rows:
        ts = r.occurred_at.strftime("%m-%d %H:%M:%S")
        et = r.event_type
        ec = (
            C_GREEN if et in ("add", "promote", "unexclude") else
            C_BLUE if et in ("demote", "remove") else
            C_GRAY if et == "exclude" else C_MAGENTA
        )
        if r.blocked_by:
            event = f"{et}[{r.blocked_by[:10]}]"
        else:
            event = et
        symbol = (r.symbol or "?")[:10]
        transition = f"{r.from_tier or '—'}→{r.to_tier or '—'}"
        rationale = truncate(r.rationale, inner)
        body.append(
            f"  {color(ts, C_CYAN):<22}  {color(f'{event:<22}', ec)}  "
            f"{symbol:<10}  {transition:<22}  {color(rationale, C_GRAY)}"
        )
    return panel("UNIVERSE EVENTS", body, width)


def render_proposals(rows: list[TradeProposal], width: int) -> list[str]:
    if not rows:
        return panel("OPEN PROPOSALS", [color("  (no open proposals)", C_DIM)], width)
    body = [
        color(
            f"  {'created':<22}  {'symbol':<10}  {'side':<5}  "
            f"{'size_usd':>10}  {'status':<10}",
            C_GRAY,
        ),
    ]
    for p in rows:
        body.append(
            f"  {color(p.proposed_at.strftime('%m-%d %H:%M:%S'), C_CYAN):<22}  "
            f"{color(p.symbol, C_CYAN):<10}  "
            f"{color(p.side, C_GREEN if p.side == 'long' else C_RED):<5}  "
            f"{float(p.size_usd):>10.2f}  "
            f"{color(p.status, C_YELLOW):<10}"
        )
    return panel("OPEN PROPOSALS", body, width)


def render_orders(rows: list[Order], width: int) -> list[str]:
    if not rows:
        return panel("RECENT ORDERS", [color("  (no orders yet)", C_DIM)], width)
    body = [
        color(
            f"  {'submitted':<22}  {'symbol':<10}  {'side':<5}  "
            f"{'status':<10}  {'fill_price':>11}",
            C_GRAY,
        ),
    ]
    for o in rows:
        sub = o.submitted_at.strftime("%m-%d %H:%M:%S") if o.submitted_at else "—"
        avg = float(o.avg_fill_price) if o.avg_fill_price is not None else None
        st_color = (
            C_GREEN if o.status == "filled" else
            C_YELLOW if o.status == "open" else
            C_RED if o.status in ("rejected", "failed") else C_DARK
        )
        body.append(
            f"  {color(sub, C_CYAN):<22}  "
            f"{color(o.symbol, C_CYAN):<10}  "
            f"{color(o.side, C_GREEN if o.side == 'long' else C_RED):<5}  "
            f"{color(o.status, st_color):<10}  "
            f"{(f'{avg:.2f}' if avg is not None else '—'):>11}"
        )
    return panel("RECENT ORDERS", body, width)


# ---------------- Main loop ------------------------------------------------

async def snapshot() -> str:
    width = term_width()
    factory = get_session_factory()
    async with factory() as session:
        last = await _last_shift(session)
        last_spawned = await _last_spawned_shift(session)
        today_count, week_count = await _shift_counts(session)
        positions = await _open_positions(session)
        shifts = await _recent_shifts(session)
        univ_events = await _recent_universe_events(session)
        proposals = await _open_proposals(session)
        orders = await _recent_orders(session)
        ks = await _kill_switch(session)

    policy = _load_policy()
    state_fm, state_body = _load_state_md()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = "  HEARTBEAT TRADER — live  "
    bar_n = max(0, (width - len(title)) // 2)
    lines: list[str] = []
    lines.append(color(f"{'═' * bar_n}{title}{'═' * bar_n}", C_MAGENTA + C_BOLD))
    lines.append(color(f"  refreshed {now}  ·  Ctrl-C to exit", C_DARK))
    lines.append("")

    lines.extend(render_trader_state(
        policy=policy, last=last, last_spawned=last_spawned,
        today_count=today_count, week_count=week_count,
        positions=positions, state_fm=state_fm, ks=ks,
        width=width,
    ))
    lines.append("")
    lines.extend(render_what_watching(
        state_body,
        last_spawned.started_at if last_spawned else None,
        width,
    ))
    lines.append("")
    lines.extend(render_universe(policy, width))
    lines.append("")
    lines.extend(render_open_positions(positions, width))
    lines.append("")
    lines.extend(render_recent_shifts(shifts, width))
    lines.append("")
    lines.extend(render_universe_events(univ_events, width))
    lines.append("")
    lines.extend(render_proposals(proposals, width))
    lines.append("")
    lines.extend(render_orders(orders, width))

    return "\n".join(lines)


async def main_async(interval: float, once: bool) -> None:
    if once:
        sys.stdout.write(await snapshot() + "\n")
        sys.stdout.flush()
        return
    while True:
        clear_screen()
        sys.stdout.write(await snapshot() + "\n")
        sys.stdout.flush()
        await asyncio.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="Live dashboard for the heartbeat trader.")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between refresh (default: 5)")
    ap.add_argument("--once", action="store_true", help="render one snapshot and exit")
    args = ap.parse_args()
    os.environ.setdefault("FORCE_COLOR", "1")
    try:
        asyncio.run(main_async(args.interval, args.once))
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
