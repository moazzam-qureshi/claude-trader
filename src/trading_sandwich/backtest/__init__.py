"""Backtest framework — Phase 3 Wave 1 Task 2.26.

Historical kline replay for mechanical strategies: walk a candle
series, tick the strategy each bar, fill the emitted OrderIntents
against that bar (with slippage + fees), track cash/position, and
produce a performance summary.

Public surface:
  Candle                       — the OHLCV bar shape (backtest.fill_sim)
  simulate_fill                 — one intent + one candle → Fill | None
  Fill                          — a simulated fill (price, qty, fee, ...)
  replay_strategy               — the replay engine → BacktestResult
  BacktestResult                — equity curve + fills + analytics
  default_price_snapshot_builder— common price/indicator snapshot
  compute_analytics             — equity curve → metrics dict

Limitations of this first cut (documented; not blockers for the
framework's purpose of catching gross strategy bugs before live):
  - rebalance-family down-sizes are modelled as buys (role='rebalance'
    doesn't encode direction; a future OrderIntent.direction field
    would close this).
  - the default snapshot builder covers price/ATR/EMA/RSI/Bollinger
    only; strategies needing exotic feeds (BTC.D, multi-TF booleans)
    need a custom snapshot_builder.
  - no funding/borrow costs (irrelevant — halal-spot, no leverage).
"""
from __future__ import annotations

from trading_sandwich.backtest.analytics import compute_analytics
from trading_sandwich.backtest.fill_sim import Candle, Fill, simulate_fill
from trading_sandwich.backtest.replay import (
    BacktestResult,
    default_price_snapshot_builder,
    replay_strategy,
)

__all__ = [
    "Candle",
    "Fill",
    "simulate_fill",
    "replay_strategy",
    "BacktestResult",
    "default_price_snapshot_builder",
    "compute_analytics",
]
