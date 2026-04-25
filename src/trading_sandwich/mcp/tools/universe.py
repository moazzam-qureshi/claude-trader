"""MCP tools for universe state and curation: get_open_positions,
assess_symbol_fit, mutate_universe."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Position
from trading_sandwich.mcp.server import mcp


POLICY_PATH = Path(os.environ.get("TS_POLICY_PATH", "/app/policy.yaml"))


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
