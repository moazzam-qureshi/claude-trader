"""Quick-look at recent Claude decisions and any open proposals."""
import asyncio

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.db.models_phase2 import Order, TradeProposal


async def show():
    factory = get_session_factory()
    async with factory() as session:
        decisions = (await session.execute(
            select(ClaudeDecision)
            .order_by(ClaudeDecision.invoked_at.desc())
            .limit(15)
        )).scalars().all()

        print("=== Recent Claude decisions ===")
        for r in decisions:
            ts = r.invoked_at.strftime("%H:%M:%S")
            sid = str(r.signal_id)[:8] if r.signal_id else "?"
            decision = r.decision or "?"
            rationale = (r.rationale or "")[:120]
            print(f"{ts}  {decision:15s}  signal={sid}  mode={r.invocation_mode}")
            print(f"  rationale: {rationale}")
            print()

        proposals = (await session.execute(
            select(TradeProposal).order_by(TradeProposal.proposed_at.desc()).limit(10)
        )).scalars().all()

        print(f"=== Trade proposals ({len(proposals)} recent) ===")
        for p in proposals:
            print(f"{p.proposed_at.strftime('%H:%M:%S')}  {p.status:10s}  "
                  f"{p.symbol} {p.side} ${p.size_usd}  rr={p.expected_rr}  "
                  f"approver={p.approved_by or '-'}")

        orders = (await session.execute(
            select(Order).order_by(Order.submitted_at.desc()).limit(10)
        )).scalars().all()

        print(f"\n=== Orders ({len(orders)} recent) ===")
        for o in orders:
            ts = o.submitted_at.strftime("%H:%M:%S") if o.submitted_at else "-"
            print(f"{ts}  {o.status:10s}  {o.execution_mode:5s}  "
                  f"{o.symbol} {o.side} ${o.size_usd}  fill={o.avg_fill_price}")


if __name__ == "__main__":
    asyncio.run(show())
