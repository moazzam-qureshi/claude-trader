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


if __name__ == "__main__":
    app()
