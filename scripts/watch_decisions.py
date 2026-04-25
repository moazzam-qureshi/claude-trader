"""Live-tail dashboard for the trading sandwich.

Polls Postgres every few seconds and renders an in-place updating view of:
  - Pipeline health (candles / features / signals / decisions / proposals / orders)
  - Today's gating breakdown
  - Today's decision split with archetype attribution
  - The 8 most recent Claude decisions with rationale snippets
  - Open trade proposals and recent orders
  - Kill-switch state

Run inside the tools container:

    docker compose run --rm tools python /app/scripts/watch_decisions.py

Or follow continuously (default 5s refresh):

    docker compose run --rm tools python /app/scripts/watch_decisions.py --interval 3
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import (
    ClaudeDecision,
    Features,
    RawCandle,
    Signal,
    SignalOutcome,
)
from trading_sandwich.db.models_phase2 import (
    KillSwitchState,
    Order,
    TradeProposal,
)


# ----- ANSI colors / styling ------------------------------------------------

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

DECISION_COLOR = {
    "paper_trade": C_GREEN,
    "alert":       C_YELLOW,
    "research_more": C_BLUE,
    "ignore":      C_GRAY,
}

STATUS_COLOR = {
    "filled":   C_GREEN,
    "open":     C_CYAN,
    "approved": C_GREEN,
    "executed": C_GREEN,
    "pending":  C_YELLOW,
    "expired":  C_DARK,
    "rejected": C_RED,
    "failed":   C_RED,
    "canceled": C_DARK,
}


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


def hr(width: int, char: str = "─", c: str = C_DARK) -> str:
    return color(char * width, c)


def panel_header(title: str, width: int) -> str:
    pad = " " * 2
    line = f"┌─{title}─" + ("─" * max(0, width - len(title) - 4)) + "┐"
    return color(line, C_DARK)


def fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def fmt_dec(d, places: int = 4) -> str:
    if d is None:
        return "—"
    try:
        return f"{float(d):.{places}f}"
    except Exception:
        return str(d)


def truncate(s: str, n: int) -> str:
    if s is None:
        return ""
    s = s.replace("\n", " ").replace("\r", "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def relative_time(ts: datetime) -> str:
    delta = (datetime.now(timezone.utc) - ts).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h {int((delta % 3600) // 60)}m ago"
    return f"{int(delta // 86400)}d ago"


# ----- Data loaders ----------------------------------------------------------

async def _counts(session) -> dict:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = {}
    out["raw_candles"] = (await session.execute(
        select(func.count()).select_from(RawCandle))).scalar_one()
    out["features"] = (await session.execute(
        select(func.count()).select_from(Features))).scalar_one()
    out["signals"] = (await session.execute(
        select(func.count()).select_from(Signal))).scalar_one()
    out["signals_today"] = (await session.execute(
        select(func.count()).select_from(Signal)
        .where(Signal.fired_at >= today))).scalar_one()
    out["decisions"] = (await session.execute(
        select(func.count()).select_from(ClaudeDecision))).scalar_one()
    out["decisions_today"] = (await session.execute(
        select(func.count()).select_from(ClaudeDecision)
        .where(ClaudeDecision.invoked_at >= today))).scalar_one()
    out["outcomes"] = (await session.execute(
        select(func.count()).select_from(SignalOutcome))).scalar_one()
    out["proposals"] = (await session.execute(
        select(func.count()).select_from(TradeProposal))).scalar_one()
    out["orders"] = (await session.execute(
        select(func.count()).select_from(Order))).scalar_one()
    return out


async def _gating_split(session) -> list[tuple[str, int]]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (await session.execute(
        select(Signal.gating_outcome, func.count())
        .where(Signal.fired_at >= today)
        .group_by(Signal.gating_outcome)
        .order_by(func.count().desc())
    )).all()
    return list(rows)


async def _decision_split(session) -> list[tuple[str, int]]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (await session.execute(
        select(ClaudeDecision.decision, func.count())
        .where(ClaudeDecision.invoked_at >= today)
        .group_by(ClaudeDecision.decision)
        .order_by(func.count().desc())
    )).all()
    return list(rows)


async def _archetype_split(session) -> list[tuple[str, str, int]]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (await session.execute(
        select(Signal.archetype, ClaudeDecision.decision, func.count())
        .join(ClaudeDecision, ClaudeDecision.signal_id == Signal.signal_id)
        .where(ClaudeDecision.invoked_at >= today)
        .group_by(Signal.archetype, ClaudeDecision.decision)
        .order_by(Signal.archetype, ClaudeDecision.decision)
    )).all()
    return list(rows)


async def _recent_decisions(session, limit: int = 8) -> list[dict]:
    rows = (await session.execute(
        select(
            ClaudeDecision.invoked_at, ClaudeDecision.decision,
            ClaudeDecision.rationale, ClaudeDecision.signal_id,
            Signal.symbol, Signal.timeframe, Signal.archetype, Signal.direction,
        )
        .join(Signal, Signal.signal_id == ClaudeDecision.signal_id, isouter=True)
        .order_by(ClaudeDecision.invoked_at.desc())
        .limit(limit)
    )).all()
    return [
        {
            "ts": r[0], "decision": r[1], "rationale": r[2],
            "sid": r[3], "symbol": r[4], "tf": r[5],
            "archetype": r[6], "direction": r[7],
        }
        for r in rows
    ]


async def _open_proposals(session, limit: int = 5) -> list[dict]:
    rows = (await session.execute(
        select(TradeProposal)
        .where(TradeProposal.status.in_(["pending", "approved"]))
        .order_by(TradeProposal.proposed_at.desc())
        .limit(limit)
    )).scalars().all()
    return [
        {
            "ts": r.proposed_at, "status": r.status,
            "symbol": r.symbol, "side": r.side, "type": r.order_type,
            "size_usd": r.size_usd, "rr": r.expected_rr,
            "approved_by": r.approved_by,
        }
        for r in rows
    ]


async def _recent_orders(session, limit: int = 5) -> list[dict]:
    rows = (await session.execute(
        select(Order)
        .order_by(Order.submitted_at.desc().nullslast())
        .limit(limit)
    )).scalars().all()
    return [
        {
            "ts": r.submitted_at, "status": r.status, "mode": r.execution_mode,
            "symbol": r.symbol, "side": r.side, "size_usd": r.size_usd,
            "fill": r.avg_fill_price,
        }
        for r in rows
    ]


async def _kill_switch(session) -> dict:
    row = (await session.execute(
        select(KillSwitchState).where(KillSwitchState.id == 1)
    )).scalar_one_or_none()
    if row is None:
        return {"active": False, "reason": None, "tripped_at": None}
    return {
        "active": bool(row.active),
        "reason": row.tripped_reason,
        "tripped_at": row.tripped_at,
    }


# ----- Renderers -------------------------------------------------------------

def render_kpi_strip(c: dict, ks: dict, w: int) -> str:
    bits = [
        f"candles {color(fmt_int(c['raw_candles']), C_BOLD)}",
        f"features {color(fmt_int(c['features']), C_BOLD)}",
        f"signals {color(fmt_int(c['signals']), C_BOLD)} ({fmt_int(c['signals_today'])} today)",
        f"decisions {color(fmt_int(c['decisions']), C_BOLD)} ({fmt_int(c['decisions_today'])} today)",
        f"outcomes {color(fmt_int(c['outcomes']), C_BOLD)}",
        f"proposals {color(fmt_int(c['proposals']), C_BOLD)}",
        f"orders {color(fmt_int(c['orders']), C_BOLD)}",
    ]
    line = "  ·  ".join(bits)
    if ks["active"]:
        line += "  ·  " + color("⏻ KILL-SWITCH ACTIVE", C_RED + C_BOLD)
    else:
        line += "  ·  " + color("⏻ kill-switch inactive", C_DARK)
    return line


def render_bar(label: str, n: int, total: int, width: int, c: str) -> str:
    if total <= 0:
        bar_w = 0
    else:
        bar_w = max(0, int(round((n / total) * width)))
    bar = color("█" * bar_w, c) + color("·" * (width - bar_w), C_DARK)
    pct = (n / total * 100) if total > 0 else 0
    return f"  {label:<22} {bar} {color(f'{n:>5,}', C_BOLD)} {color(f'({pct:5.1f}%)', C_GRAY)}"


def render_split(title: str, rows: list[tuple[str, int]], color_map: dict | None, default_color: str) -> list[str]:
    out = [color(title, C_BOLD)]
    if not rows:
        out.append(color("  (no data yet)", C_DIM))
        return out
    total = sum(n for _, n in rows)
    bar_width = 40
    for name, n in rows:
        c = (color_map or {}).get(name, default_color)
        out.append(render_bar(name or "(none)", n, total, bar_width, c))
    return out


def render_decisions_table(rows: list[dict], width: int) -> list[str]:
    out = [color("Recent Claude decisions", C_BOLD)]
    if not rows:
        out.append(color("  (none yet)", C_DIM))
        return out
    rationale_w = max(40, width - 78)
    out.append(color(
        f"  {'time':<10} {'decision':<13} {'symbol/tf':<14} {'archetype':<22} rationale", C_GRAY,
    ))
    out.append(color("  " + "─" * (width - 4), C_DARK))
    for r in rows:
        ts = r["ts"].strftime("%H:%M:%S") if r["ts"] else "—"
        rel = relative_time(r["ts"]) if r["ts"] else ""
        decision = r["decision"] or "?"
        dc = DECISION_COLOR.get(decision, C_RESET)
        sym_tf = f"{r['symbol'] or '?'}/{r['tf'] or '?'}"
        arch = f"{r['archetype'] or '?'} {r['direction'] or ''}"
        rationale = truncate(r["rationale"] or "", rationale_w)
        out.append(
            f"  {color(ts, C_DARK)} {color(f'{decision:<13}', dc)} "
            f"{color(f'{sym_tf:<14}', C_CYAN)} {arch:<22} {color(rationale, C_GRAY)}"
        )
        out.append(f"  {' ':<10} {' ':<13} {color(f'({rel})', C_DARK):<14}")
    return out


def render_proposals_table(rows: list[dict]) -> list[str]:
    out = [color("Open trade proposals (pending or approved)", C_BOLD)]
    if not rows:
        out.append(color("  (no open proposals)", C_DIM))
        return out
    out.append(color(f"  {'time':<10} {'status':<10} {'symbol':<10} {'side':<6} {'type':<8} {'size':<10} {'RR':<6} approver", C_GRAY))
    out.append(color("  " + "─" * 80, C_DARK))
    for r in rows:
        ts = r["ts"].strftime("%H:%M:%S") if r["ts"] else "—"
        sc = STATUS_COLOR.get(r["status"], C_RESET)
        status_cell = f"{r['status']:<10}"
        symbol_cell = f"{r['symbol']:<10}"
        size_str = fmt_dec(r["size_usd"], 2)
        rr_str = fmt_dec(r["rr"], 2)
        approver = r["approved_by"] or "—"
        out.append(
            f"  {color(ts, C_DARK)} {color(status_cell, sc)} "
            f"{color(symbol_cell, C_CYAN)} "
            f"{r['side']:<6} {r['type']:<8} ${size_str:<8} {rr_str:<6} {approver}"
        )
    return out


def render_orders_table(rows: list[dict]) -> list[str]:
    out = [color("Recent orders", C_BOLD)]
    if not rows:
        out.append(color("  (no orders yet)", C_DIM))
        return out
    out.append(color(f"  {'time':<10} {'status':<10} {'mode':<6} {'symbol':<10} {'side':<6} {'size':<10} fill", C_GRAY))
    out.append(color("  " + "─" * 75, C_DARK))
    for r in rows:
        ts = r["ts"].strftime("%H:%M:%S") if r["ts"] else "—"
        sc = STATUS_COLOR.get(r["status"], C_RESET)
        status_cell = f"{r['status']:<10}"
        symbol_cell = f"{r['symbol']:<10}"
        size_str = fmt_dec(r["size_usd"], 2)
        fill_str = fmt_dec(r["fill"], 4)
        out.append(
            f"  {color(ts, C_DARK)} {color(status_cell, sc)} "
            f"{r['mode']:<6} {color(symbol_cell, C_CYAN)} "
            f"{r['side']:<6} ${size_str:<8} {fill_str}"
        )
    return out


def render_archetype_split(rows: list[tuple[str, str, int]]) -> list[str]:
    out = [color("Today's archetype × decision matrix", C_BOLD)]
    if not rows:
        out.append(color("  (no decisions today)", C_DIM))
        return out
    by_arch: dict[str, dict[str, int]] = {}
    for arch, decision, n in rows:
        by_arch.setdefault(arch, {})[decision] = n
    decisions = ["paper_trade", "alert", "research_more", "ignore"]
    out.append(color(f"  {'archetype':<22} " + " ".join(f"{d:>13}" for d in decisions), C_GRAY))
    out.append(color("  " + "─" * (22 + 14 * 4), C_DARK))
    for arch in sorted(by_arch.keys()):
        cells = []
        for d in decisions:
            n = by_arch[arch].get(d, 0)
            if n == 0:
                cells.append(color(f"{'·':>13}", C_DARK))
            else:
                c = DECISION_COLOR.get(d, C_RESET)
                cells.append(color(f"{n:>13}", c))
        out.append(f"  {arch:<22} {' '.join(cells)}")
    return out


# ----- Main render loop ------------------------------------------------------

async def snapshot() -> str:
    width = term_width()
    factory = get_session_factory()
    async with factory() as session:
        c = await _counts(session)
        ks = await _kill_switch(session)
        gating = await _gating_split(session)
        decisions = await _decision_split(session)
        archetypes = await _archetype_split(session)
        recent = await _recent_decisions(session)
        proposals = await _open_proposals(session)
        orders = await _recent_orders(session)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = []
    title = "  TRADING SANDWICH — live decisions  "
    bar = "═" * max(0, (width - len(title)) // 2)
    lines.append(color(f"{bar}{title}{bar}", C_MAGENTA + C_BOLD))
    lines.append(color(f"  refreshed {now}", C_DARK))
    lines.append("")
    lines.append(render_kpi_strip(c, ks, width))
    lines.append(hr(width))
    lines.append("")

    lines.extend(render_split("Today's gating outcome split", gating, None, C_BLUE))
    lines.append("")
    lines.extend(render_split("Today's decision split", decisions, DECISION_COLOR, C_GRAY))
    lines.append("")
    lines.extend(render_archetype_split(archetypes))
    lines.append("")
    lines.extend(render_decisions_table(recent, width))
    lines.append("")
    lines.extend(render_proposals_table(proposals))
    lines.append("")
    lines.extend(render_orders_table(orders))
    lines.append("")
    lines.append(color("  Ctrl-C to exit  ·  --interval N to change refresh", C_DARK))

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
    ap = argparse.ArgumentParser(description="Live decision tail for the trading sandwich.")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between refresh (default: 5)")
    ap.add_argument("--once", action="store_true", help="render one snapshot and exit")
    args = ap.parse_args()

    # Force-color even when stdout is a docker-attached TTY.
    os.environ.setdefault("FORCE_COLOR", "1")

    try:
        asyncio.run(main_async(args.interval, args.once))
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
