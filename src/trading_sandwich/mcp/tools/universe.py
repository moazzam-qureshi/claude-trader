"""MCP tools for universe state and curation: get_open_positions,
assess_symbol_fit, mutate_universe."""
from __future__ import annotations

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Position
from trading_sandwich.mcp.server import mcp


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
