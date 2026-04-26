"""paper_match_orders — Celery Beat task that fills paper limit orders
whose limit price has been crossed by the latest 5m candle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawCandle
from trading_sandwich.db.models_phase2 import Order


async def _latest_candle(symbol: str) -> RawCandle | None:
    factory = get_session_factory()
    async with factory() as session:
        return (await session.execute(
            select(RawCandle)
            .where(RawCandle.symbol == symbol, RawCandle.timeframe == "5m")
            .order_by(RawCandle.open_time.desc())
            .limit(1)
        )).scalar_one_or_none()


async def match_async() -> int:
    """Scan open paper limit orders; fill any whose limit was crossed."""
    factory = get_session_factory()
    filled = 0
    async with factory() as session:
        opens = (await session.execute(
            select(Order).where(
                Order.execution_mode == "paper",
                Order.status == "open",
            )
        )).scalars().all()
    for o in opens:
        candle = await _latest_candle(o.symbol)
        if candle is None or o.limit_price is None:
            continue
        crossed = (
            o.side == "long" and Decimal(str(candle.low)) <= Decimal(str(o.limit_price))
        ) or (
            o.side == "short" and Decimal(str(candle.high)) >= Decimal(str(o.limit_price))
        )
        if not crossed:
            continue
        now_fill = datetime.now(timezone.utc)
        fill_price = Decimal(str(o.limit_price))
        size_base = Decimal(str(o.size_usd)) / fill_price
        async with factory() as session:
            await session.execute(
                update(Order)
                .where(Order.order_id == o.order_id)
                .values(
                    status="filled",
                    filled_at=now_fill,
                    avg_fill_price=fill_price,
                    filled_base=size_base,
                )
            )
            await session.commit()
        filled += 1

        # Discord: paper fill (Phase 2.7)
        from trading_sandwich.notifications.discord import (
            post_card_safe, render_order_filled_card,
        )
        await post_card_safe(render_order_filled_card(
            occurred_at=now_fill,
            symbol=o.symbol,
            side=o.side,
            size_base=float(size_base),
            fill_price=float(fill_price),
            notional_usd=float(o.size_usd),
            fees_usd=None,
        ))
    return filled


@app.task(name="trading_sandwich.execution.paper_match.match")
def match() -> int:
    return asyncio.run(match_async())
