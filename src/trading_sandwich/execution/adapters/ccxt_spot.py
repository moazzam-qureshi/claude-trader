"""CCXTSpotAdapter — Binance plain spot via CCXT (halal: no margin, no borrow).

Operates on Binance spot wallet only. Longs only — shorts are rejected at
this adapter as a hard backstop (you cannot sell what you do not own on spot).

The adapter is the **last gate** before Binance. CLAUDE.md tells the trader
not to propose shorts; if the prompt slips, this adapter rejects the call
before any API hit.

Notable differences from CCXTProAdapter:
- defaultType: 'spot' (no margin, no isolated)
- side='short' → immediate rejection, no Binance call
- get_account_state() reads spot balance, not isolated-margin equity
- get_positions() synthesizes "open positions" from non-USDT spot balances
  that the system itself opened (tracked via the positions table elsewhere;
  this method returns currently-held non-USDT balances as a sanity check)
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


# Stablecoin/quote currencies — never counted as "open positions"
_QUOTE_ASSETS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD"}


class CCXTSpotAdapter(ExchangeAdapter):
    """Binance plain spot. Halal — no leverage, no margin, no shorts."""

    def __init__(self) -> None:
        s = get_settings()
        self._exchange = ccxt.binance({
            "apiKey": s.binance_api_key,
            "secret": s.binance_api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
            },
        })
        self._exchange.set_sandbox_mode(s.binance_testnet)

    async def submit_order(self, request: OrderRequest) -> OrderReceipt:
        # HARD BACKSTOP: shorts are not possible on halal spot.
        if request.side == "short":
            return OrderReceipt(
                exchange_order_id=None,
                status="rejected",
                rejection_reason=(
                    "halal_spot_no_shorts: cannot sell what you do not own; "
                    "shorts require margin/borrowing which is not permitted"
                ),
            )

        ccxt_side = "buy"  # only longs reach here
        ccxt_type = {"market": "market", "limit": "limit"}[request.order_type]

        # Convert USD notional to base size at last close. For market orders
        # we use the live ticker; for limit orders we use limit_price.
        if request.limit_price:
            entry_price = Decimal(str(request.limit_price))
        else:
            ticker = await self._exchange.fetch_ticker(request.symbol)
            entry_price = Decimal(str(ticker["last"]))
        base_size = float(request.size_usd / entry_price)

        # Plain spot order — no isolated/margin params.
        params = {"newClientOrderId": request.client_order_id}

        try:
            r = await self._exchange.create_order(
                symbol=request.symbol,
                type=ccxt_type,
                side=ccxt_side,
                amount=base_size,
                price=float(request.limit_price) if request.limit_price else None,
                params=params,
            )
            # Stop-loss as a sell stop_loss_limit on the long. No margin params.
            await self._exchange.create_order(
                symbol=request.symbol,
                type="stop_loss_limit",
                side="sell",
                amount=base_size,
                price=float(request.stop_loss.value),
                params={
                    "stopPrice": float(request.stop_loss.value),
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
                exchange_order_id=None,
                status="rejected",
                rejection_reason=str(exc)[:500],
            )

    async def cancel_order(self, exchange_order_id: str) -> OrderReceipt:
        return OrderReceipt(
            exchange_order_id=exchange_order_id, status="canceled",
        )

    async def get_open_orders(self) -> list[dict]:
        orders = await self._exchange.fetch_open_orders()
        return [
            {
                "order_id": str(o.get("id")),
                "symbol": o.get("symbol"),
                "side": "long",  # spot opens are always 'buy'; we model as long
                "size_usd": Decimal(str(o.get("amount", 0))),
                "limit_price": (
                    Decimal(str(o["price"])) if o.get("price") else None
                ),
            }
            for o in orders
            if o.get("side") == "buy"
        ]

    async def get_positions(self) -> list[dict]:
        """Synthesize 'open positions' from non-USDT spot balances.

        On spot, 'positions' is conceptual — if you have BTC in your wallet,
        you have a long BTC 'position' with respect to USDT. This method
        returns each non-quote asset balance as a long position. Average
        entry is unknown from balance alone (the positions table tracks
        the system's own opens; this is a sanity check against the wallet).
        """
        try:
            balance = await self._exchange.fetch_balance({"type": "spot"})
        except Exception:
            return []
        out = []
        totals = balance.get("total", {}) or {}
        for asset, total in totals.items():
            if asset in _QUOTE_ASSETS:
                continue
            try:
                amount = Decimal(str(total))
            except Exception:
                continue
            if amount <= 0:
                continue
            out.append({
                "symbol": f"{asset}USDT",
                "side": "long",
                "size_base": amount,
                "avg_entry": Decimal("0"),  # unknown from balance alone
                "unrealized_pnl_usd": Decimal("0"),
            })
        return out

    async def get_account_state(self) -> AccountState:
        """Read spot wallet. equity_usd = USDT free balance + total non-USDT
        valued at last ticker.

        For halal spot, 'free_margin_usd' is a misnomer carried over from
        the parent interface — semantically it is 'free buying power in USDT'.
        """
        try:
            balance = await self._exchange.fetch_balance({"type": "spot"})
        except Exception:
            return AccountState(
                equity_usd=Decimal("0"),
                free_margin_usd=Decimal("0"),
                unrealized_pnl_usd=Decimal("0"),
                realized_pnl_today_usd=Decimal("0"),
                open_positions_count=0,
                leverage_used=Decimal("0"),
            )

        usdt = balance.get("USDT", {}) or {}
        usdt_free = Decimal(str(usdt.get("free", 0) or 0))
        usdt_total = Decimal(str(usdt.get("total", 0) or 0))

        # Approximate equity by summing USDT free + non-USDT valued in USDT
        # at last ticker. Best-effort; failures fall back to USDT only.
        equity = usdt_total
        non_usdt_count = 0
        totals = balance.get("total", {}) or {}
        for asset, total in totals.items():
            if asset in _QUOTE_ASSETS:
                continue
            try:
                amount = Decimal(str(total))
            except Exception:
                continue
            if amount <= 0:
                continue
            non_usdt_count += 1
            try:
                ticker = await self._exchange.fetch_ticker(f"{asset}/USDT")
                last = Decimal(str(ticker.get("last") or 0))
                equity += amount * last
            except Exception:
                # Asset has no USDT pair (e.g., dust). Skip valuation.
                continue

        return AccountState(
            equity_usd=equity,
            free_margin_usd=usdt_free,  # actually 'free buying power in USDT'
            unrealized_pnl_usd=Decimal("0"),
            realized_pnl_today_usd=Decimal("0"),
            open_positions_count=non_usdt_count,
            leverage_used=Decimal("0"),  # halal spot — leverage is always 0
        )
