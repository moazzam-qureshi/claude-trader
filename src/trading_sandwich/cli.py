"""Typer CLI. Phase 0: doctor + stats. Expands with Claude-invoking commands
in Phase 2."""
from __future__ import annotations

import asyncio

import typer
from sqlalchemy import text

from trading_sandwich.db.engine import get_engine
from trading_sandwich.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = typer.Typer(help="Trading Sandwich CLI")

_PHASE_0_TABLES = [
    "raw_candles",
    "features",
    "signals",
    "signal_outcomes",
    "claude_decisions",
]


@app.command()
def doctor() -> None:
    """Check DB + basic invariants. Exit non-zero on failure."""
    async def _check() -> None:
        engine = get_engine()
        try:
            async with engine.connect() as conn:
                v = (await conn.execute(text("SELECT 1"))).scalar()
                assert v == 1
                tables = (await conn.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                ))).scalars().all()
                for need in _PHASE_0_TABLES:
                    assert need in tables, f"missing table: {need}"
        finally:
            await engine.dispose()

    try:
        asyncio.run(_check())
    except Exception as exc:
        typer.echo(f"doctor: FAIL — {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("doctor: database OK, all Phase 0 tables present")


@app.command()
def stats() -> None:
    """Show row counts for every Phase 0 table."""
    async def _counts() -> None:
        engine = get_engine()
        try:
            async with engine.connect() as conn:
                for tbl in _PHASE_0_TABLES:
                    n = (await conn.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar()
                    typer.echo(f"{tbl}: {n}")
        finally:
            await engine.dispose()
    asyncio.run(_counts())


@app.command()
def proposals(
    status: str = typer.Option(None, help="Filter: pending|approved|rejected|expired|executed|failed"),
) -> None:
    """List trade_proposals rows."""
    async def _list() -> None:
        from sqlalchemy import select
        from trading_sandwich.db.models_phase2 import TradeProposal
        engine = get_engine()
        try:
            async with engine.connect() as conn:
                stmt = select(TradeProposal)
                if status:
                    stmt = stmt.where(TradeProposal.status == status)
                stmt = stmt.order_by(TradeProposal.proposed_at.desc()).limit(50)
                rows = (await conn.execute(stmt)).all()
                for r in rows:
                    typer.echo(
                        f"{r.proposal_id} {r.status} {r.symbol} {r.side} "
                        f"${r.size_usd} expected_rr={r.expected_rr}"
                    )
        finally:
            await engine.dispose()
    asyncio.run(_list())


@app.command()
def orders(status: str = typer.Option(None)) -> None:
    """List orders rows."""
    async def _list() -> None:
        from sqlalchemy import select
        from trading_sandwich.db.models_phase2 import Order
        engine = get_engine()
        try:
            async with engine.connect() as conn:
                stmt = select(Order)
                if status:
                    stmt = stmt.where(Order.status == status)
                stmt = stmt.order_by(Order.submitted_at.desc()).limit(50)
                rows = (await conn.execute(stmt)).all()
                for r in rows:
                    typer.echo(
                        f"{r.order_id} {r.status} {r.execution_mode} "
                        f"{r.symbol} {r.side} ${r.size_usd}"
                    )
        finally:
            await engine.dispose()
    asyncio.run(_list())


@app.command()
def positions() -> None:
    """List open positions."""
    async def _list() -> None:
        from sqlalchemy import select
        from trading_sandwich.db.models_phase2 import Position
        engine = get_engine()
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(
                    select(Position).where(Position.closed_at.is_(None))
                )).all()
                for r in rows:
                    typer.echo(
                        f"{r.symbol} {r.side} size={r.size_base} entry={r.avg_entry} "
                        f"unrealized={r.unrealized_pnl_usd}"
                    )
        finally:
            await engine.dispose()
    asyncio.run(_list())


trading_app = typer.Typer(help="Kill-switch control")
app.add_typer(trading_app, name="trading")


@trading_app.command("status")
def trading_status() -> None:
    """Print kill-switch state."""
    async def _check():
        from trading_sandwich.execution.kill_switch import is_active
        active = await is_active()
        typer.echo(f"kill_switch: {'ACTIVE (trading paused)' if active else 'inactive'}")
    asyncio.run(_check())


@trading_app.command("pause")
def trading_pause(reason: str = typer.Option(..., "--reason", help="Why pause?")) -> None:
    """Trip the kill-switch (stops new orders)."""
    async def _trip():
        from trading_sandwich.execution.kill_switch import trip
        await trip(reason=f"manual_pause: {reason}")
        typer.echo(f"trading paused — {reason}")
    asyncio.run(_trip())


@trading_app.command("resume")
def trading_resume(
    ack_reason: str = typer.Option(..., "--ack-reason", help="Acknowledgement"),
) -> None:
    """Resume trading from kill-switch."""
    async def _resume():
        from trading_sandwich.execution.kill_switch import resume
        await resume(ack_reason=ack_reason)
        typer.echo(f"trading resumed — {ack_reason}")
    asyncio.run(_resume())


@app.command()
def calibration(lookback_days: int = typer.Option(30)) -> None:
    """Show median 24h return for alert vs ignore decisions."""
    async def _report():
        from trading_sandwich.execution.calibration import calibration_report
        r = await calibration_report(lookback_days=lookback_days)
        typer.echo(f"lookback: {r['lookback_days']}d")
        typer.echo(f"alert    n={r['alert_count']}  median_24h_ret={r['alert_median_24h']}")
        typer.echo(f"ignore   n={r['ignore_count']}  median_24h_ret={r['ignore_median_24h']}")
    asyncio.run(_report())


if __name__ == "__main__":
    app()
