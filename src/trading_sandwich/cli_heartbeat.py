"""Heartbeat CLI subcommands: status, shifts, universe, universe events."""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer
import yaml
from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift, UniverseEvent


heartbeat_app = typer.Typer(help="Heartbeat trader inspection commands")
universe_app = typer.Typer(help="Universe state + events")
heartbeat_app.add_typer(universe_app, name="universe")


@heartbeat_app.command("status")
def status() -> None:
    """Show last shift's pacing, today/week counts, and current STATE.md
    frontmatter values."""
    async def _run() -> None:
        factory = get_session_factory()
        async with factory() as session:
            last = (await session.execute(
                select(HeartbeatShift)
                .order_by(HeartbeatShift.started_at.desc())
                .limit(1)
            )).scalars().first()
        if last is None:
            typer.echo("no shifts recorded yet")
        else:
            typer.echo(f"last shift: {last.started_at.isoformat()}")
            typer.echo(f"  spawned: {last.spawned}  exit_reason: {last.exit_reason}")
            typer.echo(f"  next_check_in_minutes: {last.next_check_in_minutes}")
            typer.echo(f"  next_check_reason: {last.next_check_reason}")

        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
        async with factory() as session:
            spawned_today = (await session.execute(
                select(func.count(HeartbeatShift.id)).where(
                    HeartbeatShift.spawned.is_(True),
                    HeartbeatShift.started_at >= today_start,
                )
            )).scalar_one()
            spawned_week = (await session.execute(
                select(func.count(HeartbeatShift.id)).where(
                    HeartbeatShift.spawned.is_(True),
                    HeartbeatShift.started_at >= week_start,
                )
            )).scalar_one()
        typer.echo(f"spawned today: {spawned_today}")
        typer.echo(f"spawned this week: {spawned_week}")

        state_path = Path("/app/runtime/STATE.md")
        if state_path.exists():
            try:
                import frontmatter
                post = frontmatter.load(str(state_path))
                typer.echo("---")
                typer.echo(f"STATE shift_count: {post.metadata.get('shift_count')}")
                typer.echo(f"STATE regime: {post.metadata.get('regime')}")
                typer.echo(f"STATE open_positions: {post.metadata.get('open_positions')}")
                typer.echo(f"STATE next_check_in_minutes: {post.metadata.get('next_check_in_minutes')}")
            except Exception as exc:
                typer.echo(f"STATE.md unreadable: {exc}")

    asyncio.run(_run())


@heartbeat_app.command("shifts")
def shifts(limit: int = typer.Option(20, "--limit", "-n")) -> None:
    """Print last N rows of heartbeat_shifts."""
    async def _run() -> None:
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(HeartbeatShift)
                .order_by(HeartbeatShift.started_at.desc())
                .limit(limit)
            )).scalars().all()
        if not rows:
            typer.echo("no shifts")
            return
        typer.echo(f"{'started_at':<28}  {'spawned':<8}  {'exit_reason':<14}  "
                   f"{'actual':<7}  {'next':<5}  {'dur':<5}")
        for r in rows:
            typer.echo(
                f"{r.started_at.isoformat():<28}  "
                f"{str(r.spawned):<8}  "
                f"{(r.exit_reason or '-'):<14}  "
                f"{(str(r.actual_interval_min) if r.actual_interval_min is not None else '-'):<7}  "
                f"{(str(r.next_check_in_minutes) if r.next_check_in_minutes is not None else '-'):<5}  "
                f"{(str(r.duration_seconds) if r.duration_seconds is not None else '-'):<5}"
            )
    asyncio.run(_run())


@universe_app.command("show")
def universe_show() -> None:
    """Print current policy.yaml::universe.tiers snapshot."""
    raw = yaml.safe_load(Path("/app/policy.yaml").read_text())
    tiers = raw["universe"]["tiers"]
    for tier in ("core", "watchlist", "observation", "excluded"):
        symbols = tiers.get(tier, {}).get("symbols", [])
        typer.echo(f"{tier:<12} ({len(symbols):>2}): {', '.join(symbols) or '(empty)'}")


@universe_app.command("events")
def universe_events(limit: int = typer.Option(20, "--limit", "-n")) -> None:
    """Print last N rows from universe_events."""
    async def _run() -> None:
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(UniverseEvent)
                .order_by(UniverseEvent.occurred_at.desc())
                .limit(limit)
            )).scalars().all()
        if not rows:
            typer.echo("no events")
            return
        typer.echo(f"{'occurred_at':<28}  {'event_type':<20}  {'symbol':<10}  "
                   f"{'from→to':<25}  {'rationale':<60}")
        for r in rows:
            transition = f"{r.from_tier or '-'}→{r.to_tier or '-'}"
            blocked = f" [{r.blocked_by}]" if r.blocked_by else ""
            rationale = (r.rationale or "")[:60]
            typer.echo(
                f"{r.occurred_at.isoformat():<28}  "
                f"{r.event_type + blocked:<20}  "
                f"{r.symbol:<10}  "
                f"{transition:<25}  "
                f"{rationale:<60}"
            )
    asyncio.run(_run())
