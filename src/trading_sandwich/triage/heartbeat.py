"""Heartbeat scheduler — gating worker + Celery task wrapper.

Pattern: Celery Beat fires every 15 min. The task reads STATE.md and the
heartbeat_shifts history, decides whether to spawn Claude (per pacing rules),
and either spawns + records, or records a skipped row and exits.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift


POLICY_PATH = Path(os.environ.get("TS_POLICY_PATH", "/app/policy.yaml"))


@dataclass
class PacingInputs:
    last_spawned_at: datetime | None
    last_requested_interval_min: int | None
    spawned_today: int
    spawned_this_week: int


def _prompt_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd="/app"
        ).strip()
    except Exception:
        return "unknown"


async def _query_pacing_inputs() -> PacingInputs:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    async with factory() as session:
        last = (await session.execute(
            select(HeartbeatShift)
            .where(HeartbeatShift.spawned.is_(True))
            .order_by(HeartbeatShift.started_at.desc())
            .limit(1)
        )).scalars().first()
        spawned_today = (await session.execute(
            select(func.count(HeartbeatShift.id))
            .where(
                HeartbeatShift.spawned.is_(True),
                HeartbeatShift.started_at >= today_start,
            )
        )).scalar_one()
        spawned_this_week = (await session.execute(
            select(func.count(HeartbeatShift.id))
            .where(
                HeartbeatShift.spawned.is_(True),
                HeartbeatShift.started_at >= week_start,
            )
        )).scalar_one()
    return PacingInputs(
        last_spawned_at=last.started_at if last else None,
        last_requested_interval_min=last.next_check_in_minutes if last else None,
        spawned_today=spawned_today,
        spawned_this_week=spawned_this_week,
    )


async def record_skipped_shift(
    *,
    actual_interval_min: int | None,
    exit_reason: str,
    prompt_version: str,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(HeartbeatShift(
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            actual_interval_min=actual_interval_min,
            spawned=False,
            exit_reason=exit_reason,
            prompt_version=prompt_version,
        ))
        await session.commit()


def load_pacing_config():
    from trading_sandwich.triage.pacing import PacingConfig
    raw = yaml.safe_load(POLICY_PATH.read_text())
    hb = raw["heartbeat"]
    return PacingConfig(
        min_minutes=hb["interval_minutes"]["min"],
        max_minutes=hb["interval_minutes"]["max"],
        daily_cap=hb["daily_shift_cap"],
        weekly_cap=hb["weekly_shift_cap"],
    )


# ---------------------------------------------------------------------------
# Heartbeat tick — the Celery Beat target.
# ---------------------------------------------------------------------------

ALLOWED_TOOLS = [
    "mcp__tsandwich__get_signal",
    "mcp__tsandwich__get_market_snapshot",
    "mcp__tsandwich__find_similar_signals",
    "mcp__tsandwich__get_archetype_stats",
    "mcp__tsandwich__save_decision",
    "mcp__tsandwich__send_alert",
    "mcp__tsandwich__propose_trade",
    "mcp__tsandwich__read_diary",
    "mcp__tsandwich__write_state",
    "mcp__tsandwich__append_diary",
    "mcp__tsandwich__mutate_universe",
    "mcp__tsandwich__assess_symbol_fit",
    "mcp__tsandwich__get_open_positions",
    "mcp__tsandwich__get_universe",
    "mcp__tsandwich__get_recent_signals",
    "mcp__tsandwich__get_top_movers",
    "mcp__tsandwich__get_pipeline_health",
    "mcp__tradingview",
    "mcp__binance__binanceAccountInfo",
    "mcp__binance__binanceOrderBook",
    "mcp__binance__binanceAccountSnapshot",
]


RUNTIME_DIR = Path(os.environ.get("TS_RUNTIME_DIR", "/app/runtime"))
MCP_CONFIG_PATH = Path(os.environ.get("TS_MCP_CONFIG", "/app/.mcp.json"))


# Module-level alias so tests can monkeypatch this name to a fake.
async def _spawn_claude_shift(*, argv, cwd, timeout_seconds):
    from trading_sandwich.triage.shift_invocation import spawn_claude_shift
    return await spawn_claude_shift(
        argv=argv, cwd=cwd, timeout_seconds=timeout_seconds,
    )


async def heartbeat_tick() -> None:
    """One pass of the heartbeat scheduler.

    1. Compute pacing inputs from DB.
    2. Decide whether to spawn (gating).
    3. If skip: insert a skipped row, return.
    4. If spawn: insert spawned row, build argv, run subprocess with timeout,
       update row with outcome (state snapshot, next pacing directive).
    """
    from sqlalchemy import text as _sql_text

    from trading_sandwich.triage.pacing import decide_whether_to_spawn
    from trading_sandwich.triage.shift_invocation import build_claude_argv
    from trading_sandwich.triage.state_io import read_state

    pv = _prompt_version()
    cfg = load_pacing_config()
    inputs = await _query_pacing_inputs()
    decision = decide_whether_to_spawn(
        cfg=cfg,
        last_spawned_at=inputs.last_spawned_at,
        last_requested_interval_min=inputs.last_requested_interval_min,
        spawned_today=inputs.spawned_today,
        spawned_this_week=inputs.spawned_this_week,
    )
    if not decision.spawn:
        await record_skipped_shift(
            actual_interval_min=decision.actual_interval_min,
            exit_reason=decision.exit_reason or "skipped",
            prompt_version=pv,
        )
        return

    today = datetime.now(timezone.utc).date()
    today_diary = RUNTIME_DIR / "diary" / f"{today.isoformat()}.md"
    today_diary.parent.mkdir(parents=True, exist_ok=True)
    if not today_diary.exists():
        today_diary.write_text(f"# Diary — {today.isoformat()}\n", encoding="utf-8")

    argv = build_claude_argv(
        runtime_dir=RUNTIME_DIR,
        today_diary=today_diary,
        mcp_config_path=MCP_CONFIG_PATH,
        allowed_tools=ALLOWED_TOOLS,
    )

    started_at = datetime.now(timezone.utc)
    factory = get_session_factory()
    async with factory() as session:
        row = HeartbeatShift(
            started_at=started_at,
            requested_interval_min=inputs.last_requested_interval_min,
            actual_interval_min=decision.actual_interval_min,
            interval_clamped=decision.interval_clamped,
            spawned=True,
            prompt_version=pv,
        )
        session.add(row)
        await session.commit()
        shift_id = row.id

    timeout_seconds = yaml.safe_load(POLICY_PATH.read_text())["heartbeat"][
        "shift_timeout_seconds"
    ]
    result = await _spawn_claude_shift(
        argv=argv, cwd=RUNTIME_DIR, timeout_seconds=timeout_seconds,
    )

    state_snapshot = ""
    next_check_in = None
    next_check_reason = None
    state_path = RUNTIME_DIR / "STATE.md"
    if state_path.exists():
        try:
            fm, _body = read_state(state_path)
            state_snapshot = state_path.read_text()
            next_check_in = fm.next_check_in_minutes
            next_check_reason = fm.next_check_reason
        except Exception:
            pass

    if result.returncode == 0:
        exit_reason = "completed"
    elif result.stderr == "timeout":
        exit_reason = "timeout"
    else:
        exit_reason = "error"

    async with factory() as session:
        await session.execute(_sql_text(
            "UPDATE heartbeat_shifts "
            "SET ended_at=:ended, duration_seconds=:dur, "
            "    next_check_in_minutes=:nci, next_check_reason=:ncr, "
            "    state_snapshot=:snap, exit_reason=:er "
            "WHERE id=:id"
        ).bindparams(
            ended=datetime.now(timezone.utc),
            dur=result.duration_seconds,
            nci=next_check_in,
            ncr=next_check_reason,
            snap=state_snapshot,
            er=exit_reason,
            id=shift_id,
        ))
        await session.commit()

    # Discord: announce heartbeat shift errors (Phase 2.7). Successful
    # shifts stay silent — too noisy.
    if exit_reason in ("error", "timeout"):
        from trading_sandwich.notifications.discord import (
            post_card_safe, render_heartbeat_error_card,
        )
        stderr = (result.stderr or "")[:400]
        await post_card_safe(render_heartbeat_error_card(
            occurred_at=datetime.now(timezone.utc),
            exit_reason=exit_reason,
            duration_seconds=result.duration_seconds,
            stderr_excerpt=stderr,
        ))


# ---------------------------------------------------------------------------
# Celery task wrappers — registered in celery_app.py beat_schedule.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from celery import shared_task  # noqa: E402


@shared_task(name="trading_sandwich.triage.heartbeat.heartbeat_tick_celery")
def heartbeat_tick_celery() -> None:
    asyncio.run(heartbeat_tick())


@shared_task(name="trading_sandwich.triage.heartbeat.discord_retry_sweep_celery")
def discord_retry_sweep_celery() -> None:
    from trading_sandwich.notifications.discord import retry_unposted_events
    asyncio.run(retry_unposted_events())


# ---------------------------------------------------------------------------
# Daily summary — fires once per UTC day, posts a one-card recap
# ---------------------------------------------------------------------------

async def post_daily_summary() -> None:
    from datetime import timedelta as _td
    from sqlalchemy import func
    from trading_sandwich.db.models_phase2 import (
        Order as _Order, Position as _Position, TradeProposal as _TradeProposal,
    )
    from trading_sandwich.db.models_heartbeat import (
        UniverseEvent as _UniverseEvent,
    )
    from trading_sandwich.notifications.discord import (
        post_card_safe, render_daily_summary_card,
    )

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    factory = get_session_factory()
    async with factory() as session:
        shifts = (await session.execute(
            select(func.count(HeartbeatShift.id)).where(
                HeartbeatShift.spawned.is_(True),
                HeartbeatShift.started_at >= day_start,
            )
        )).scalar_one()
        proposals = (await session.execute(
            select(func.count(_TradeProposal.proposal_id)).where(
                _TradeProposal.proposed_at >= day_start,
            )
        )).scalar_one()
        orders_filled = (await session.execute(
            select(func.count(_Order.order_id)).where(
                _Order.filled_at >= day_start,
                _Order.status == "filled",
            )
        )).scalar_one()
        orders_rejected = (await session.execute(
            select(func.count(_Order.order_id)).where(
                _Order.submitted_at >= day_start,
                _Order.status.in_(("rejected", "failed")),
            )
        )).scalar_one()
        universe_changes = (await session.execute(
            select(func.count(_UniverseEvent.id)).where(
                _UniverseEvent.occurred_at >= day_start,
                _UniverseEvent.event_type != "hard_limit_blocked",
            )
        )).scalar_one()
        open_positions_count = (await session.execute(
            select(func.count()).select_from(_Position).where(
                _Position.closed_at.is_(None),
            )
        )).scalar_one()

    # Equity from the live adapter (best-effort).
    equity_usd = 0.0
    realized_pnl_today = 0.0
    try:
        from trading_sandwich.execution.worker import _adapter
        adapter, _mode = _adapter()
        state = await adapter.get_account_state()
        equity_usd = float(state.equity_usd)
        realized_pnl_today = float(state.realized_pnl_today_usd)
    except Exception:
        pass

    await post_card_safe(render_daily_summary_card(
        occurred_at=now,
        shifts=shifts,
        proposals=proposals,
        orders_filled=orders_filled,
        orders_rejected=orders_rejected,
        universe_changes=universe_changes,
        open_positions=open_positions_count,
        realized_pnl_usd=realized_pnl_today,
        equity_usd=equity_usd,
    ))


@shared_task(name="trading_sandwich.triage.heartbeat.daily_summary_celery")
def daily_summary_celery() -> None:
    asyncio.run(post_daily_summary())


# ---------------------------------------------------------------------------
# State-drift detection helper
# ---------------------------------------------------------------------------

async def detect_state_drift(state_path: Path) -> dict:
    """Compare STATE.md frontmatter open_positions to live DB count.

    Returned shape: {state_says, db_says, drift}. The shift uses this in
    the ORIENT step (CLAUDE.md §1.3); on drift, DB wins and STATE is rewritten.
    """
    from trading_sandwich.mcp.tools.universe import get_open_positions
    from trading_sandwich.triage.state_io import read_state

    fm, _ = read_state(state_path)
    db_positions = await get_open_positions()
    state_says = fm.open_positions
    db_says = len(db_positions)
    drift = state_says != db_says

    if drift:
        # Discord: drift detected (Phase 2.7)
        from trading_sandwich.notifications.discord import (
            post_card_safe, render_state_drift_card,
        )
        await post_card_safe(render_state_drift_card(
            occurred_at=datetime.now(timezone.utc),
            state_says=state_says,
            db_says=db_says,
        ))

    return {
        "state_says": state_says,
        "db_says": db_says,
        "drift": drift,
    }
