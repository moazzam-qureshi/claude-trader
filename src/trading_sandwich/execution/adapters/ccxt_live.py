"""CCXTProAdapter — Binance spot margin (Isolated) via CCXT.

Operates on Binance spot with Isolated margin, supporting longs (borrow USDT)
and shorts (borrow asset). Up to 3x leverage per policy.yaml.

Live integration is exercised manually only. The wrapper survives without
real keys (used by structural test and rail #15 dry-runs).
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
    """Binance spot Isolated margin. Class name kept for adapter-loader
    compatibility (`_adapter()` in worker.py).
    """

    def __init__(self) -> None:
        s = get_settings()
        self._exchange = ccxt.binance({
            "apiKey": s.binance_api_key,
            "secret": s.binance_api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "margin",
                "marginMode": "isolated",
            },
        })
        self._exchange.set_sandbox_mode(s.binance_testnet)

    async def submit_order(self, request: OrderRequest) -> OrderReceipt:
        ccxt_side = "buy" if request.side == "long" else "sell"
        ccxt_type = {"market": "market", "limit": "limit"}[request.order_type]

        # Convert USD notional to base size at last close. For market orders
        # we use the live ticker; for limit orders we use limit_price.
        if request.limit_price:
            entry_price = Decimal(str(request.limit_price))
        else:
            ticker = await self._exchange.fetch_ticker(request.symbol)
            entry_price = Decimal(str(ticker["last"]))
        base_size = float(request.size_usd / entry_price)

        # Isolated-margin order params: auto-borrow on entry, auto-repay on exit.
        side_effect = "MARGIN_BUY" if request.side == "long" else "AUTO_REPAY"
        params = {
            "newClientOrderId": request.client_order_id,
            "isIsolated": "TRUE",
            "sideEffectType": side_effect,
        }

        try:
            r = await self._exchange.create_order(
                symbol=request.symbol,
                type=ccxt_type,
                side=ccxt_side,
                amount=base_size,
                price=float(request.limit_price) if request.limit_price else None,
                params=params,
            )
            # Attach reduceOnly stop on the opposite side. Spot Isolated supports
            # OCO-style stops via stopLossLimit; for now we use stop-market.
            stop_side = "sell" if request.side == "long" else "buy"
            stop_side_effect = "AUTO_REPAY" if request.side == "long" else "MARGIN_BUY"
            await self._exchange.create_order(
                symbol=request.symbol,
                type="stop_loss_limit",
                side=stop_side,
                amount=base_size,
                price=float(request.stop_loss.value),
                params={
                    "stopPrice": float(request.stop_loss.value),
                    "isIsolated": "TRUE",
                    "sideEffectType": stop_side_effect,
                    "newClientOrderId": f"stop-{request.client_order_id}",
                },
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
        orders = await self._exchange.fetch_open_orders(params={"isIsolated": "TRUE"})
        return [
            {"order_id": str(o.get("id")), "symbol": o.get("symbol"),
             "side": "long" if o.get("side") == "buy" else "short",
             "size_usd": Decimal(str(o.get("amount", 0))),
             "limit_price": (Decimal(str(o["price"])) if o.get("price") else None)}
            for o in orders
        ]

    async def get_positions(self) -> list[dict]:
        # Spot margin reports positions as a borrow + asset balance pair.
        # Synthesize "open positions" by checking isolated-margin account state.
        try:
            account = await self._exchange.sapi_get_margin_isolated_account()
        except Exception:
            return []
        out = []
        for asset in account.get("assets", []):
            base = asset.get("baseAsset", {})
            quote = asset.get("quoteAsset", {})
            base_borrow = Decimal(str(base.get("borrowed", 0) or 0))
            base_free = Decimal(str(base.get("free", 0) or 0))
            quote_borrow = Decimal(str(quote.get("borrowed", 0) or 0))
            symbol = asset.get("symbol", "")

            if base_free > 0 and quote_borrow > 0:
                # Long: bought asset with borrowed USDT
                out.append({
                    "symbol": symbol,
                    "side": "long",
                    "size_base": base_free,
                    "avg_entry": (quote_borrow / base_free) if base_free else Decimal("0"),
                    "unrealized_pnl_usd": Decimal("0"),
                })
            elif base_borrow > 0:
                # Short: borrowed asset, sold for USDT
                out.append({
                    "symbol": symbol,
                    "side": "short",
                    "size_base": base_borrow,
                    "avg_entry": Decimal("0"),
                    "unrealized_pnl_usd": Decimal("0"),
                })
        return out

    async def get_account_state(self) -> AccountState:
        try:
            account = await self._exchange.sapi_get_margin_isolated_account()
        except Exception:
            return AccountState(
                equity_usd=Decimal("0"),
                free_margin_usd=Decimal("0"),
                unrealized_pnl_usd=Decimal("0"),
                realized_pnl_today_usd=Decimal("0"),
                open_positions_count=0,
                leverage_used=Decimal("0"),
            )
        total_btc = Decimal(str(account.get("totalNetAssetOfBtc", 0) or 0))
        # Convert BTC → USD via ticker (rough; for precision use a separate fetch)
        try:
            ticker = await self._exchange.fetch_ticker("BTC/USDT")
            btc_usd = Decimal(str(ticker["last"]))
        except Exception:
            btc_usd = Decimal("0")
        equity_usd = total_btc * btc_usd
        return AccountState(
            equity_usd=equity_usd,
            free_margin_usd=equity_usd,
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_today_usd=Decimal("0"),
            open_positions_count=len(account.get("assets", [])),
            leverage_used=Decimal("0"),
        )
