"""Render the Discord proposal-card embed + component buttons."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID


def render_proposal_embed(
    *,
    proposal_id: UUID,
    symbol: str, side: str, archetype: str, timeframe: str,
    size_usd: Decimal, entry: Decimal,
    stop: Decimal, stop_atr_mult: Decimal,
    tp: Decimal | None, expected_rr: Decimal,
    worst_case_loss_usd: Decimal, worst_case_pct_equity: Decimal,
    similar_count: int, similar_win_rate: Decimal | None, similar_median_r: str,
    opportunity: str, risk: str, profit_case: str,
    alignment: str, similar_trades_evidence: str,
    expires_at: datetime,
) -> dict:
    title = (
        f"📈 PROPOSAL — {symbol} {side.upper()} · {archetype} ({timeframe})"
    )
    tp_text = f" · TP {tp} ({expected_rr}R)" if tp is not None else ""
    desc = (
        f"Size ${size_usd} · Entry ~${entry} · Stop ${stop} ({stop_atr_mult}·ATR){tp_text}"
    )
    win_rate_text = f"{similar_win_rate:.0%}" if similar_win_rate is not None else "n/a"
    return {
        "title": title,
        "description": desc,
        "fields": [
            {"name": "OPPORTUNITY", "value": opportunity, "inline": False},
            {"name": f"RISK — worst-case loss ${worst_case_loss_usd} ({worst_case_pct_equity}% equity)",
             "value": risk, "inline": False},
            {"name": f"PROFIT CASE — expected RR {expected_rr}",
             "value": profit_case, "inline": False},
            {"name": "ALIGNMENT", "value": alignment, "inline": False},
            {"name": f"EVIDENCE — {similar_count} similar trades · {win_rate_text} win rate · median {similar_median_r}",
             "value": similar_trades_evidence, "inline": False},
        ],
        "footer": {"text": f"Expires {expires_at:%H:%M UTC} · proposal_id {str(proposal_id)[:8]}"},
        "components": [{
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Approve", "emoji": {"name": "✅"},
                 "custom_id": f"approve:{proposal_id}"},
                {"type": 2, "style": 4, "label": "Reject", "emoji": {"name": "❌"},
                 "custom_id": f"reject:{proposal_id}"},
                {"type": 2, "style": 2, "label": "Details", "emoji": {"name": "🔎"},
                 "custom_id": f"details:{proposal_id}"},
            ],
        }],
    }
