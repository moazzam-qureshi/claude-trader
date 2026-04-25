"""PaperAdapter — simulates fills against the live candle feed.

Market orders fill at the latest 5m candle close. Limit orders are marked
'open' and matched by paper_match.py (Celery Beat). Stop attachment is
enforced at the worker level (the adapter just receives a request that
already has stop_loss set).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from trading_sandwich import _policy
from trading_sandwich.contracts.phase2 import (
    AccountState,
    OrderRequest,
    OrderReceipt,
)
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawCandle
from trading_sandwich.execution.adapters.base import ExchangeAdapter


async def _latest_close_price(symbol: str) -> Decimal | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(RawCandle.close)
            .where(RawCandle.symbol == symbol, RawCandle.timeframe == "5m")
            .order_by(RawCandle.open_time.desc())
            .limit(1)
        )).scalar_one_or_none()
        return Decimal(str(row)) if row is not None else None


class PaperAdapter(ExchangeAdapter):
    async def submit_order(self, request: OrderRequest) -> OrderReceipt:
        last = await _latest_close_price(request.symbol)
        if last is None:
            return OrderReceipt(
                exchange_order_id=None, status="rejected",
                rejection_reason="no_price_data",
            )
        if request.order_type == "market":
            return OrderReceipt(
                exchange_order_id=f"paper-{uuid.uuid4().hex[:12]}",
                status="filled",
                avg_fill_price=last,
                filled_base=request.size_usd / last,
                fees_usd=Decimal("0"),
            )
        return OrderReceipt(
            exchange_order_id=f"paper-{uuid.uuid4().hex[:12]}",
            status="open",
        )

    async def cancel_order(self, exchange_order_id: str) -> OrderReceipt:
        return OrderReceipt(
            exchange_order_id=exchange_order_id, status="canceled",
        )

    async def get_open_orders(self) -> list[dict]:
        from trading_sandwich.db.models_phase2 import Order
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(Order).where(
                    Order.execution_mode == "paper",
                    Order.status == "open",
                )
            )).scalars().all()
            return [
                {"order_id": str(r.order_id), "symbol": r.symbol,
                 "side": r.side, "size_usd": r.size_usd,
                 "limit_price": r.limit_price}
                for r in rows
            ]

    async def get_positions(self) -> list[dict]:
        from trading_sandwich.db.models_phase2 import Position
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(Position).where(Position.closed_at.is_(None))
            )).scalars().all()
            return [
                {"symbol": r.symbol, "side": r.side,
                 "size_base": r.size_base, "avg_entry": r.avg_entry,
                 "unrealized_pnl_usd": r.unrealized_pnl_usd}
                for r in rows
            ]

    async def get_account_state(self) -> AccountState:
        seed = _policy.get_paper_starting_equity_usd()
        return AccountState(
            equity_usd=seed,
            free_margin_usd=seed,
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_today_usd=Decimal("0"),
            open_positions_count=0,
            leverage_used=Decimal("0"),
        )
