"""Backtest fill simulator — Phase 3 Wave 1 Task 2.26.

Turns one OrderIntent + one candle into a Fill (or None if it
wouldn't fill that bar):

  market buy  → fills at close * (1 + slippage_bps/10000), fee deducted
  market sell → fills at close * (1 - slippage_bps/10000), fee deducted
  limit buy   → fills iff candle.low  <= limit_price (at limit_price)
  limit sell  → fills iff candle.high >= limit_price (at limit_price)

`stop` orders aren't emitted by any current strategy; if one shows up
it's treated like a market order (filled at close ± slippage). Fees
apply to limit fills too (taker-equivalent — conservative).

Trade direction is derived from OrderIntent.role:
  entry, rebalance      → buy
  exit, take_profit, stop_loss → sell
(rebalance-as-buy is a known limitation — see backtest/__init__.py.)

A Fill is denominated so a position book can be updated directly:
  side       — 'buy' | 'sell'
  role       — the OrderIntent.role (audit passthrough)
  fill_price — execution price
  qty        — base units = gross_usd / fill_price
  gross_usd  — the requested notional
  fee_usd    — fee charged (fee_bps of gross_usd)
  net_usd    — signed cash impact: -(gross+fee) on a buy, +(gross-fee)
               on a sell
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_sandwich.strategies.base import OrderIntent


_BPS = Decimal("10000")
_BUY_ROLES = {"entry", "rebalance"}
_SELL_ROLES = {"exit", "take_profit", "stop_loss"}


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. open_time is the bar's start (and the timestamp
    fed to the strategy as snapshot['now'] by the replay engine)."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Fill:
    side: str          # 'buy' | 'sell'
    role: str
    fill_price: Decimal
    qty: Decimal
    gross_usd: Decimal
    fee_usd: Decimal
    net_usd: Decimal   # signed cash impact
    client_order_id: str


def _side_for_role(role: str) -> str:
    if role in _SELL_ROLES:
        return "sell"
    if role in _BUY_ROLES:
        return "buy"
    # Defensive default — unknown role treated as a buy.
    return "buy"


def simulate_fill(
    intent: OrderIntent,
    candle: Candle,
    *,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> Fill | None:
    """Simulate the fill of `intent` against `candle`. Returns the
    Fill, or None if a limit order wouldn't have been touched this bar."""
    side = _side_for_role(intent.role)
    gross = intent.size_usd

    if intent.order_type == "limit":
        if intent.limit_price is None:
            raise ValueError("limit order requires limit_price")
        lp = intent.limit_price
        if side == "buy":
            if candle.low > lp:
                return None
            fill_price = lp
        else:  # sell
            if candle.high < lp:
                return None
            fill_price = lp
    else:  # market (or 'stop' — treated as market)
        slip = slippage_bps / _BPS
        if side == "buy":
            fill_price = candle.close * (Decimal("1") + slip)
        else:
            fill_price = candle.close * (Decimal("1") - slip)

    qty = gross / fill_price
    fee = gross * fee_bps / _BPS
    if side == "buy":
        net = -(gross + fee)
    else:
        net = gross - fee

    return Fill(
        side=side,
        role=intent.role,
        fill_price=fill_price,
        qty=qty,
        gross_usd=gross,
        fee_usd=fee,
        net_usd=net,
        client_order_id=intent.client_order_id,
    )
