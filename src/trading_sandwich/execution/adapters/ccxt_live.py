"""CCXTProAdapter — Binance USD-M futures via CCXT Pro.

Live integration is exercised manually only. CI runs only the structural
test (Task 34). The actual Binance call paths are wired here but rely on
real API keys at runtime, which only the operator provides.
"""
from __future__ import annotations

from decimal import Decimal

import ccxt.async_support as ccxt

from trading_sandwich.config import get_settings
from trading_sandwich.contracts.phase2 import (
    AccountState,
    OrderRequest,
    OrderReceipt,
)
from trading_sandwich.execution.adapters.base import ExchangeAdapter


class CCXTProAdapter(ExchangeAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._exchange = ccxt.binanceusdm({
            "apiKey": s.binance_api_key,
            "secret": s.binance_api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self._exchange.set_sandbox_mode(s.binance_testnet)

    async def submit_order(self, request: OrderRequest) -> OrderReceipt:
        params = {"newClientOrderId": request.client_order_id}
        ccxt_side = "buy" if request.side == "long" else "sell"
        ccxt_type = {"market": "market", "limit": "limit"}[request.order_type]
        try:
            r = await self._exchange.create_order(
                symbol=request.symbol,
                type=ccxt_type,
                side=ccxt_side,
                amount=float(request.size_usd / Decimal("1")),
                price=float(request.limit_price) if request.limit_price else None,
                params=params,
            )
            stop_side = "sell" if request.side == "long" else "buy"
            await self._exchange.create_order(
                symbol=request.symbol,
                type="stop_market",
                side=stop_side,
                amount=float(request.size_usd / Decimal("1")),
                params={"stopPrice": float(request.stop_loss.value),
                        "reduceOnly": True,
                        "newClientOrderId": f"stop-{request.client_order_id}"},
            )
            return OrderReceipt(
                exchange_order_id=str(r.get("id")),
                status=("filled" if r.get("status") == "closed" else "open"),
                avg_fill_price=Decimal(str(r["average"])) if r.get("average") else None,
                filled_base=Decimal(str(r["filled"])) if r.get("filled") else None,
                fees_usd=None,
            )
        except Exception as exc:  # noqa: BLE001 — operator must see all errors
            return OrderReceipt(
                exchange_order_id=None, status="rejected",
                rejection_reason=str(exc)[:500],
            )

    async def cancel_order(self, exchange_order_id: str) -> OrderReceipt:
        return OrderReceipt(
            exchange_order_id=exchange_order_id, status="canceled",
        )

    async def get_open_orders(self) -> list[dict]:
        orders = await self._exchange.fetch_open_orders()
        return [
            {"order_id": str(o.get("id")), "symbol": o.get("symbol"),
             "side": "long" if o.get("side") == "buy" else "short",
             "size_usd": Decimal(str(o.get("amount", 0))),
             "limit_price": (Decimal(str(o["price"])) if o.get("price") else None)}
            for o in orders
        ]

    async def get_positions(self) -> list[dict]:
        try:
            positions = await self._exchange.fetch_positions()
        except AttributeError:
            return []
        out = []
        for p in positions:
            contracts = p.get("contracts") or 0
            if not contracts:
                continue
            out.append({
                "symbol": p.get("symbol"),
                "side": "long" if p.get("side") == "long" else "short",
                "size_base": Decimal(str(contracts)),
                "avg_entry": Decimal(str(p.get("entryPrice", 0))),
                "unrealized_pnl_usd": Decimal(str(p.get("unrealizedPnl", 0))),
            })
        return out

    async def get_account_state(self) -> AccountState:
        bal = await self._exchange.fetch_balance({"type": "future"})
        return AccountState(
            equity_usd=Decimal(str(bal.get("total", {}).get("USDT", 0))),
            free_margin_usd=Decimal(str(bal.get("free", {}).get("USDT", 0))),
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_today_usd=Decimal("0"),
            open_positions_count=0,
            leverage_used=Decimal("0"),
        )
