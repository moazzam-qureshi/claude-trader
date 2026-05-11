"""Backtest replay engine — Phase 3 Wave 1 Task 2.26.

replay_strategy() walks a candle series, ticks the strategy each bar,
fills the emitted OrderIntents against that bar via the fill
simulator, threads a cash/position book through, and records an
equity point per bar. Returns a BacktestResult: the equity curve,
the fills, the realised round-trip PnLs (FIFO buy→sell matching),
and the analytics dict.

The strategy's ctx.state persists between bars in-memory (a plain
dict), exactly as the worker persists it via strategy_state. The
caller supplies a snapshot_builder: (candle, prior_candles, state) ->
dict, so the engine doesn't need to know each strategy's data needs.
default_price_snapshot_builder covers the common price / ATR / EMA /
RSI / Bollinger fields; strategies needing exotic feeds want a custom
builder.

Resting limit orders: a limit intent that doesn't fill on the bar it
was emitted stays *open* and is re-checked against every subsequent
bar until it fills (or the run ends) — modelling the exchange's order
book the way a real grid relies on. A strategy re-emitting an order
with a client_order_id that's already open is deduped (no duplicate
resting order). Market intents fill immediately on their emit bar.

Sizing note: a strategy that wants to sell its whole position emits a
market intent whose size_usd is the *USD value* to liquidate. The
engine treats a sell's size_usd as a USD notional and converts to qty
at the fill price; if that qty would exceed the held position it's
capped at the position (no shorting — halal-spot inviolable, enforced
here too as a backstop). A sell never produces negative position
units.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

import pandas as pd

from trading_sandwich.backtest.analytics import compute_analytics
from trading_sandwich.backtest.fill_sim import Candle, Fill, simulate_fill
from trading_sandwich.indicators.trend import compute_ema, compute_rsi
from trading_sandwich.indicators.volatility import compute_atr, compute_bollinger
from trading_sandwich.strategies.base import OrderIntent, Strategy, StrategyContext


SnapshotBuilder = Callable[[Candle, list[Candle], dict], dict]


@dataclass
class BacktestResult:
    equity_curve: list[Decimal]
    fills: list[Fill]
    roundtrip_pnls: list[Decimal]
    analytics: dict
    final_cash_usd: Decimal
    final_position_units: Decimal


@dataclass
class _Book:
    """Cash + position with FIFO lot tracking for round-trip PnL."""

    cash: Decimal
    units: Decimal = Decimal("0")
    # FIFO lots: deque of (qty, cost_per_unit_incl_fee)
    _lots: deque = field(default_factory=deque)
    roundtrip_pnls: list[Decimal] = field(default_factory=list)

    def apply_buy(self, fill: Fill) -> None:
        self.cash += fill.net_usd  # negative
        self.units += fill.qty
        # cost per unit including the fee share
        total_cost = fill.gross_usd + fill.fee_usd
        cost_per_unit = total_cost / fill.qty if fill.qty > 0 else Decimal("0")
        self._lots.append([fill.qty, cost_per_unit])

    def apply_sell(self, fill: Fill) -> Fill:
        """Apply a sell, capping qty at the held position. Returns the
        (possibly capped) fill actually applied. Records realised PnL
        against FIFO lots."""
        sell_qty = min(fill.qty, self.units)
        if sell_qty <= Decimal("0"):
            # nothing to sell — no-op fill
            return Fill(
                side="sell", role=fill.role, fill_price=fill.fill_price,
                qty=Decimal("0"), gross_usd=Decimal("0"),
                fee_usd=Decimal("0"), net_usd=Decimal("0"),
                client_order_id=fill.client_order_id,
            )
        # Proceeds for the actually-sold qty, net of the proportional fee.
        proceeds_gross = sell_qty * fill.fill_price
        # fee scales with the sold fraction of the requested gross
        fee = (
            fill.fee_usd * (sell_qty / fill.qty) if fill.qty > 0
            else Decimal("0")
        )
        proceeds_net = proceeds_gross - fee
        self.cash += proceeds_net
        self.units -= sell_qty

        # Match against FIFO lots to realise PnL.
        remaining = sell_qty
        matched_cost = Decimal("0")
        while remaining > Decimal("0") and self._lots:
            lot_qty, cost_per_unit = self._lots[0]
            take = min(lot_qty, remaining)
            matched_cost += take * cost_per_unit
            lot_qty -= take
            remaining -= take
            if lot_qty <= Decimal("0"):
                self._lots.popleft()
            else:
                self._lots[0][0] = lot_qty
        self.roundtrip_pnls.append(proceeds_net - matched_cost)

        return Fill(
            side="sell", role=fill.role, fill_price=fill.fill_price,
            qty=sell_qty, gross_usd=proceeds_gross, fee_usd=fee,
            net_usd=proceeds_net, client_order_id=fill.client_order_id,
        )

    def equity(self, mark_price: Decimal) -> Decimal:
        return self.cash + self.units * mark_price


def replay_strategy(
    *,
    strategy: Strategy,
    candles: list[Candle],
    params: dict[str, Any],
    initial_capital_usd: Decimal,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    snapshot_builder: SnapshotBuilder,
    symbol: str = "BTCUSDT",
    strategy_id: int = 1,
) -> BacktestResult:
    if not candles:
        raise ValueError("candles must be non-empty")

    book = _Book(cash=initial_capital_usd)
    state: dict = {}
    fills: list[Fill] = []
    equity_curve: list[Decimal] = []
    # Resting limit orders keyed by client_order_id (dedupe re-emits).
    open_orders: dict[str, OrderIntent] = {}

    def _apply(raw: Fill) -> None:
        if raw.side == "buy":
            book.apply_buy(raw)
            fills.append(raw)
        else:
            applied = book.apply_sell(raw)
            if applied.qty > Decimal("0"):
                fills.append(applied)

    for i, candle in enumerate(candles):
        # 1. Try to fill resting limit orders against this bar.
        for coid in list(open_orders):
            raw = simulate_fill(
                open_orders[coid], candle,
                fee_bps=fee_bps, slippage_bps=slippage_bps,
            )
            if raw is not None:
                _apply(raw)
                del open_orders[coid]

        # 2. Tick the strategy.
        prior = candles[:i]
        snapshot = snapshot_builder(candle, prior, state)
        ctx = StrategyContext(
            strategy_id=strategy_id,
            strategy_type=getattr(strategy, "__class__").__name__,
            symbol=symbol,
            params=dict(params),
            state=state,
            capital_allocated_usd=initial_capital_usd,
            capital_deployed_usd=book.units * candle.close,
        )
        intents: list[OrderIntent] = strategy.tick(ctx, snapshot=snapshot)
        state = ctx.state  # carry whatever the strategy mutated

        # 3. Handle the new intents: market fills now; limit rests
        #    (and is also given a chance to fill on this same bar).
        for intent in intents:
            if intent.order_type == "limit":
                if intent.client_order_id in open_orders:
                    continue  # dedupe a re-emitted resting order
                raw = simulate_fill(
                    intent, candle,
                    fee_bps=fee_bps, slippage_bps=slippage_bps,
                )
                if raw is not None:
                    _apply(raw)
                else:
                    open_orders[intent.client_order_id] = intent
            else:
                raw = simulate_fill(
                    intent, candle,
                    fee_bps=fee_bps, slippage_bps=slippage_bps,
                )
                if raw is not None:
                    _apply(raw)

        equity_curve.append(book.equity(candle.close))

    analytics = compute_analytics(
        equity_curve=equity_curve,
        num_trades=len(fills),
        roundtrip_pnls=list(book.roundtrip_pnls),
    )
    return BacktestResult(
        equity_curve=equity_curve,
        fills=fills,
        roundtrip_pnls=list(book.roundtrip_pnls),
        analytics=analytics,
        final_cash_usd=book.cash,
        final_position_units=book.units,
    )


# --------------------------------------------------------------------------
# Default snapshot builder
# --------------------------------------------------------------------------


def _closes_series(prior: list[Candle], current: Candle) -> pd.Series:
    closes = [float(c.close) for c in prior] + [float(current.close)]
    return pd.Series(closes, dtype=float)


def _ohlc_frame(prior: list[Candle], current: Candle) -> pd.DataFrame:
    rows = prior + [current]
    return pd.DataFrame({
        "high": [float(r.high) for r in rows],
        "low": [float(r.low) for r in rows],
        "close": [float(r.close) for r in rows],
    })


def _last_finite(series: pd.Series, fallback: Decimal) -> Decimal:
    """Last non-NaN value of `series` as a Decimal, else `fallback`."""
    s = series.dropna()
    if len(s) == 0:
        return fallback
    return Decimal(str(s.iloc[-1]))


def default_price_snapshot_builder(
    candle: Candle, prior: list[Candle], state: dict,
) -> dict:
    """Snapshot covering the common price / ATR / EMA / RSI / Bollinger
    fields. On warm-up bars (too little history for an indicator
    window) it falls back to sane values — close for EMAs/MAs, a small
    ATR estimate, RSI 50, bands at ±2% — so the replay degrades rather
    than crashing. A strategy needing fields outside this set wants a
    custom snapshot_builder.

    Fields produced: mid_price, now, reference_price, atr, atr_pct,
    ma_fast, ma_slow, ma_n, rsi, bb_lower, bb_upper.
    """
    close = candle.close
    snap: dict = {
        "mid_price": close,
        "now": candle.open_time,
        "reference_price": prior[-1].close if prior else close,
    }

    closes = _closes_series(prior, candle)
    ohlc = _ohlc_frame(prior, candle)

    # EMAs. fast/slow for the crossover (20 / 55); ma_n a longer-
    # horizon filter (50) — long enough to be a trend filter, short
    # enough to warm up within a typical backtest window. (A
    # production snapshot builder can pick a longer N if desired.)
    ema_fast = compute_ema(closes, 20)
    ema_slow = compute_ema(closes, 55)
    ema_n = compute_ema(closes, 50)
    snap["ma_fast"] = _last_finite(ema_fast, close)
    snap["ma_slow"] = _last_finite(ema_slow, close)
    snap["ma_n"] = _last_finite(ema_n, close)

    # RSI 14
    rsi = compute_rsi(closes, 14)
    snap["rsi"] = _last_finite(rsi, Decimal("50"))

    # ATR 14 (price terms) + atr_pct (fraction of price)
    atr = compute_atr(ohlc["high"], ohlc["low"], ohlc["close"], 14)
    # warm-up fallback: 1% of price
    atr_val = _last_finite(atr, close * Decimal("0.01"))
    if atr_val <= Decimal("0"):
        atr_val = close * Decimal("0.01")
    snap["atr"] = atr_val
    snap["atr_pct"] = atr_val / close if close > 0 else Decimal("0.01")

    # Bollinger 20 / 2
    bb_up, _bb_mid, bb_lo, _bb_w = compute_bollinger(closes, 20, 2.0)
    snap["bb_upper"] = _last_finite(bb_up, close * Decimal("1.02"))
    snap["bb_lower"] = _last_finite(bb_lo, close * Decimal("0.98"))

    return snap
