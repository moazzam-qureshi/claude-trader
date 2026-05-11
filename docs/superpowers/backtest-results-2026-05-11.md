# Wave 1 Strategy Backtests — 2026-05-11

Smoke backtests of the Wave 1 strategy set on **synthetic** candle
paths, run through `trading_sandwich.backtest.replay_strategy` with the
`default_price_snapshot_builder`. Fees 5 bps, slippage 2 bps,
$1000 starting capital.

These are **sanity checks, not performance claims** — the candle paths
are hand-constructed (a sine-wave chop, a linear uptrend), not real
market data, and the default snapshot builder uses warm-up fallbacks
for indicator windows. Real-data backtests over `raw_candles` come
once the snapshot-plumbing supporting task lands the production
feature feed. The goal here is "the framework runs each strategy end
to end and the equity curve / analytics are coherent", which it does.

## Representative results

| Strategy | Path | total return % | trades | max DD % |
|---|---|---|---|---|
| A1 Standard Grid (`grid_standard`) | sine chop ±5% around 50000, 47.5k–52.5k grid, 6 levels | +3.5 | 3 | 5.0 |
| D4 Time-Series Momentum (`trend_time_series_momentum`) | linear uptrend 100→300, 200 bars | +50.5 | 1 | 0.03 |
| D1 MA Crossover (`trend_ma_crossover`) | linear uptrend 100→300, 200 bars | +47.2 | 1 | 0.03 |

(The grid harvests the chop; the trend followers ride the ramp. On a
flat or above-grid path the grid records zero fills and flat equity —
verified in `tests/unit/test_backtest_real_strategy.py`.)

## Framework coverage

`tests/unit/test_backtest_*.py` — 27 tests:
- `test_backtest_fill_sim.py` — market/limit fill logic, slippage,
  fees, role→side mapping, validation.
- `test_backtest_analytics.py` — return %, drawdown, win rate, edge
  cases (flat curve, single point, empty).
- `test_backtest_replay.py` — replay engine: state persistence
  between bars, fee/slippage application, the default snapshot
  builder's fields and short-history fallbacks.
- `test_backtest_real_strategy.py` — A1 Standard Grid and D4
  Time-Series Momentum run through the full framework against
  production strategy code (resting limit orders, round-trip PnL).

## Known limitations of this first cut

- **rebalance-family down-sizes are modelled as buys.** `role='rebalance'`
  doesn't encode trade direction; the rebalance/E3/F1/G1 strategies emit
  it for both up- and down-sizes. Their *upsizing* backtests correctly;
  a trim is mis-modelled. (A future `OrderIntent.direction` field closes
  this — needed for the live execution rail too, so it's not
  backtest-specific.)
- **The default snapshot builder covers price/ATR/EMA/RSI/Bollinger only.**
  Strategies needing exotic feeds — D5 Multi-TF Alignment
  (`bullish_1d/4h/1h`), E3 BTC Dominance (`btc_dominance_rising`) — need
  a custom `snapshot_builder` and richer data; not exercised by the
  default smoke runs.
- **No real-data backtests yet.** Pending the snapshot-plumbing
  supporting task that wires `raw_candles` + the production feature
  feed into a replay-compatible candle stream.
- **No funding/borrow costs** — irrelevant: halal-spot, no leverage.
