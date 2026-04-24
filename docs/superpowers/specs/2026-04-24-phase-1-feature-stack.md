# Phase 1 — Full Feature Stack, All Archetypes, Regime Classification

> **Status:** Draft — awaiting user review
> **Author:** Claude (brainstormed 2026-04-24)
> **Predecessor:** `2026-04-21-trading-sandwich-design.md` (Phase 0 spec)
> **Architecture reference:** `/architecture.md`

---

## 1. Goal

Phase 0 proved the pipeline. Phase 1 turns it into a signal generator worth Claude's attention in Phase 2.

Specifically, Phase 1:

- Grows the indicator set from 3 (EMA/RSI/ATR) to ~25 (trend, momentum, volatility, volume, structure, microstructure).
- Adds a **rule-based regime classifier** that labels every `features` row with a `trend_regime` and `vol_regime`, so detectors can gate by market state instead of firing blindly.
- Ships **all 8 archetypes** (up from 1), each gated by appropriate regime.
- Expands outcome measurement from 2 horizons (15m, 1h) to **all 6** (15m, 1h, 4h, 24h, 3d, 7d).
- Expands the universe from 2 × 2 (BTCUSDT, ETHUSDT × 1m, 5m) to **8 × 5** (top-8 USDT perps × 5m, 15m, 1h, 4h, 1d). 1m is dropped; 5m becomes the finest timeframe.
- Adds operational capacity: **TA-Lib** (pinned Debian package), **pgbouncer**, Celery Beat gap-scan + backfill, feature-worker replica scaling.
- Adds **dedup gating** across correlated signals so Phase 2's Claude triage cap isn't burned by one market move producing 10 near-identical signals.

Phase 1 still ships no execution, no Claude, no ML. Those are Phase 2+.

---

## 2. What stays from Phase 0

These are locked decisions — they do not change in Phase 1:

- All-Python stack, CCXT Pro for Binance, pandas-ta (now + TA-Lib) for indicators.
- 5 long-lived workers: ingestor, feature-worker, signal-worker, outcome-worker; execution-worker still deferred to Phase 3. Celery Beat runs separately.
- Celery + Redis task queue. Alembic for every schema change.
- SQLAlchemy 2.0 async + asyncpg, Pydantic v2 contracts, structlog, Typer CLI, Prometheus + Grafana.
- Raw data kept forever. Every decision leaves a trace. Every prompt/policy change is a git commit.
- Testcontainers for integration tests; tests run via `docker compose run --rm test`.
- `raw_candles`, `features`, `signals`, `signal_outcomes`, `claude_decisions` table shapes from Phase 0. New columns added via Alembic migrations; existing columns don't change semantics.

---

## 3. Indicator set (the "full list")

Computed per (symbol, timeframe) by the feature-worker on each candle close. Warm-up windows vary; a `features` row is only emitted once all required lookback windows are satisfied (see §5.2).

### 3.1 Trend / momentum
- EMA(8, 21, 55, 200) — four periods
- MACD(12, 26, 9) — line, signal, histogram (3 values)
- ADX(14) + DI+(14) + DI−(14) — 3 values
- RSI(14) — 1 value
- Stochastic RSI(14, 3, 3) — K and D (2 values)
- ROC(10) — 1 value

### 3.2 Volatility / range
- ATR(14) — 1 value
- Bollinger Bands(20, 2σ) — upper, middle, lower, width (4 values)
- Keltner Channel(20, 2·ATR) — upper, middle, lower (3 values)
- Donchian Channel(20) — upper, middle, lower (3 values)

### 3.3 Volume / flow
- OBV — 1 value
- VWAP — session-anchored, daily reset at 00:00 UTC (1 value)
- Volume z-score(20) — 1 value
- MFI(14) — 1 value

### 3.4 Price-structure
- Most recent swing high + swing low (5-bar fractal) — 2 values
- Daily pivot levels (classic): P, R1, R2, S1, S2 — 5 values
- Prior-day high / low — 2 values
- Prior-week high / low — 2 values

### 3.5 Futures microstructure (ingested separately, joined to `features` at compute time)
- Funding rate — latest 8h settlement value + trailing 24h mean (2 values)
- Open interest — absolute + Δ vs 1h ago + Δ vs 24h ago (3 values)
- Long/short account ratio — Binance's `topLongShortAccountRatio` (1 value)
- Order-book imbalance at ±0.5% depth — bid-side depth / total depth (1 value)

### 3.6 Derived regime features (inputs to the classifier in §4)
- EMA-21 slope over last 10 bars (bps/bar)
- ATR percentile (current ATR vs 100-bar distribution)
- BB-width percentile (100-bar distribution) — squeeze detector
- ADX value (reused from §3.1)

### 3.7 Implementation notes

- **Library choice:** where pandas-ta and TA-Lib both implement an indicator, TA-Lib wins (faster, better-tested). TA-Lib is installed via a pinned Debian package in the Dockerfile; no source build (Phase 0 learning — TA-Lib 0.4.0 source fails to build on Debian trixie).
- **OB-imbalance** requires a separate L2 depth WebSocket subscription per symbol (`@depth20@100ms`). This is a **new ingestor path** (`ingestor/binance_depth_stream.py`), writing to a new `raw_orderbook_snapshots` table at ~10/sec per symbol. The feature-worker reads the most recent snapshot at candle close time to compute the imbalance value. Snapshots older than 24h are auto-pruned (Celery Beat daily job).
- **Funding + OI + long/short ratio** come from Binance REST endpoints, polled on a Celery Beat schedule (1/min for funding, 1/5min for OI and L/S ratio). Stored in new `raw_funding`, `raw_open_interest`, `raw_long_short_ratio` tables. The feature-worker joins the most recent value at candle close.

---

## 4. Regime classifier

### 4.1 Shape

Two independent axes, rule-based, transparent, thresholds in `policy.yaml`. Every `features` row gets both labels set (they're already nullable columns from Phase 0).

### 4.2 `trend_regime` ∈ {`trend_up`, `trend_down`, `range`}

```
trend_up   ⇔  close > ema_55 AND ema_21_slope_bps_per_bar > trend_slope_threshold_bps AND adx_14 > adx_trend_threshold
trend_down ⇔  close < ema_55 AND ema_21_slope_bps_per_bar < -trend_slope_threshold_bps AND adx_14 > adx_trend_threshold
range      ⇔  neither of the above
```

Defaults in `policy.yaml`:
```yaml
regime:
  trend_slope_threshold_bps: 2.0     # EMA-21 must gain/lose 2bps per bar
  adx_trend_threshold: 20            # classic ADX cutoff
```

### 4.3 `vol_regime` ∈ {`squeeze`, `normal`, `expansion`}

```
squeeze   ⇔  bb_width_percentile_100 < squeeze_percentile
expansion ⇔  bb_width_percentile_100 > expansion_percentile
normal    ⇔  neither
```

Defaults:
```yaml
regime:
  squeeze_percentile: 20             # bottom 20% of 100-bar BB-width = squeeze
  expansion_percentile: 80           # top 20% = expansion
```

### 4.4 Why rule-based, not ML

Decided 2026-04-24 during brainstorming. Summary: no training data exists yet. An ML classifier trained on auto-generated rule labels is circular; an ML classifier trained on hand-labeled data needs 2,000–5,000 labeled candles (expensive one-time + recurring retrain). The rule-based classifier *builds the dataset* that a future ML replacement will train on: every regime-labeled candle paired with its subsequent signal outcomes is a training row. When that dataset is large enough (e.g. ≥10k outcome rows per regime label), a Phase N spec replaces the rule-based producer with an ML model. Schema + contracts are unchanged; only the classifier internals swap.

---

## 5. Archetypes (all 8)

### 5.1 The list

Phase 0 `Archetype` Literal is expanded in `contracts/models.py`:

| Archetype | Fires only when | Direction |
|---|---|---|
| `trend_pullback` | `trend_regime ∈ {trend_up, trend_down}` AND `vol_regime ∈ {normal, expansion}` | long in `trend_up`, short in `trend_down` |
| `squeeze_breakout` | `vol_regime` transitioning `squeeze` → `expansion`, with a confirmation bar (close held outside band for 2 consecutive bars) | long if break above BB-upper, short if break below BB-lower |
| `divergence_rsi` | `vol_regime ∈ {normal, expansion}`, any trend | counter-trend: long on bullish div in `trend_down`, short on bearish div in `trend_up` |
| `divergence_macd` | `vol_regime ∈ {normal, expansion}`, any trend | counter-trend; uses MACD histogram for divergence |
| `range_rejection` | `trend_regime == range` AND `vol_regime == normal` | long on bounce off range low (Donchian-20 lower, wick touches AND closes back inside), short on rejection at range high (Donchian-20 upper, same wick+close rule) |
| `liquidity_sweep_daily` | any regime; price wicks beyond prior-day high/low then closes back inside | opposite the sweep: wick through prior-day high → short |
| `liquidity_sweep_swing` | any regime; price wicks beyond 20-bar swing high/low then closes back inside | opposite the sweep |
| `funding_extreme` | `vol_regime ∈ {normal, expansion}` AND funding beyond per-symbol threshold | counter-funding: long when funding < lower threshold, short when > upper |

Each detector is a pure function in `src/trading_sandwich/signals/detectors/<archetype>.py`, input `list[FeaturesRow]`, output `Signal | None`. Signal worker iterates the registered detectors per (symbol, timeframe) on each features close.

### 5.2 Detector warm-up

Each detector declares a `min_history` (number of bars of features context it needs). The signal worker skips detectors whose `min_history` exceeds available rows. Typical values: `trend_pullback` 22, `squeeze_breakout` 50, `divergence_*` 40, `range_rejection` 50, `liquidity_sweep_*` 30, `funding_extreme` 3.

### 5.3 `policy.yaml` additions

```yaml
per_archetype_confidence_threshold:
  trend_pullback: 0.70
  squeeze_breakout: 0.70
  divergence_rsi: 0.65
  divergence_macd: 0.65
  range_rejection: 0.65
  liquidity_sweep_daily: 0.70
  liquidity_sweep_swing: 0.65
  funding_extreme: 0.70

per_archetype_cooldown_minutes:
  trend_pullback: 30
  squeeze_breakout: 60
  divergence_rsi: 30
  divergence_macd: 30
  range_rejection: 30
  liquidity_sweep_daily: 60
  liquidity_sweep_swing: 30
  funding_extreme: 120

# Per-symbol funding thresholds (long fires below `long`, short fires above `short`).
# Symbols not listed fall back to the `default` entry.
per_symbol_funding_threshold:
  BTCUSDT:  {long: -0.0003, short: 0.0003}
  ETHUSDT:  {long: -0.0005, short: 0.0005}
  SOLUSDT:  {long: -0.0010, short: 0.0010}
  BNBUSDT:  {long: -0.0005, short: 0.0005}
  XRPUSDT:  {long: -0.0010, short: 0.0010}
  DOGEUSDT: {long: -0.0010, short: 0.0010}
  ADAUSDT:  {long: -0.0010, short: 0.0010}
  AVAXUSDT: {long: -0.0010, short: 0.0010}
  default:  {long: -0.0005, short: 0.0005}
```

---

## 6. Gating

Three stages applied in order inside the signal worker, before the signal row is persisted.

### 6.1 Threshold gate
Phase 0 behaviour unchanged: `signal.confidence < per_archetype_confidence_threshold[archetype]` → `gating_outcome = below_threshold`, persisted, no further processing.

### 6.2 Cooldown gate
Phase 0 behaviour unchanged: within `per_archetype_cooldown_minutes[archetype]` of the last `claude_triaged` signal for the same (symbol, archetype), set `gating_outcome = cooldown_suppressed`.

### 6.3 Dedup gate (new in Phase 1)

Prevents one underlying market move from producing many near-identical signals that would later burn Claude's per-day triage cap.

Rule: if a **strictly higher-timeframe** signal for the **same (symbol, direction)** has a `claude_triaged` row within the last `dedup_window_minutes`, the current signal is marked `gating_outcome = dedup_suppressed`. Timeframe ordering: `1d > 4h > 1h > 15m > 5m`. Same-timeframe duplicates are handled by the cooldown gate (§6.2), not this rule.

Defaults:
```yaml
gating:
  dedup_window_minutes: 30
```

Note: dedup-suppressed signals are still persisted (they're data — e.g. "this 5m signal agrees with the 1h trigger"). Only the `gating_outcome` changes; the rest of the row is identical to what would have been written.

### 6.4 Gating outcome precedence

Precedence = order of evaluation. Stages are applied strictly in the order §6.1 → §6.2 → §6.3; the first stage that assigns a non-triaged outcome short-circuits and that outcome is persisted. So a signal failing the threshold is recorded as `below_threshold` even if it would also be dedup-suppressed — the threshold is the more fundamental reason. Only signals that pass all three gates are recorded as `claude_triaged`.

---

## 7. Outcomes — all 6 horizons

Every `claude_triaged` signal schedules 6 `measure_outcome` Celery tasks on the `outcomes` queue with increasing `countdown`:

```
15m  → 15 * 60 = 900s
1h   → 3600s
4h   → 14400s
24h  → 86400s
3d   → 259200s
7d   → 604800s
```

`signal_outcomes` table schema is unchanged from Phase 0 (primary key `(signal_id, horizon)`); `horizon` is typed as `Literal["15m","1h","4h","24h","3d","7d"]` already in the contracts.

Operational note: 3d and 7d outcomes mean long-running Celery countdowns. Celery Beat is the producer; the worker respects `task_reject_on_worker_lost=True` (already set in Phase 0) so a worker restart doesn't lose scheduled work. `countdown` > 24h is persisted to Redis and re-queued on beat restart via `celery-redbeat` (new dependency). Phase 0's in-memory Beat scheduler would lose 24h+ countdowns across restarts; redbeat fixes that.

---

## 8. Universe

### 8.1 Phase 1 list

```yaml
universe:
  - BTCUSDT
  - ETHUSDT
  - SOLUSDT
  - BNBUSDT
  - XRPUSDT
  - DOGEUSDT
  - ADAUSDT
  - AVAXUSDT
timeframes:
  - 5m
  - 15m
  - 1h
  - 4h
  - 1d
```

8 symbols × 5 timeframes = 40 OHLCV streams, plus 8 L2 depth streams, plus REST polls for funding/OI/LSR. Total WS stream count well under one Binance connection's 1024-stream limit.

### 8.2 Adding a symbol

Manual: edit `policy.yaml`, git commit, restart the ingestor + workers. Auto-add / auto-delist is Phase 1.5+ scope.

---

## 9. Infrastructure additions

### 9.1 TA-Lib
Dockerfile installs `libta-lib0` + `libta-lib-dev` via `apt-get` (pinned Debian testing packages). **No source build.** Python bindings via `TA-Lib` wheel.

### 9.2 pgbouncer
New service in `docker-compose.yml`, session-pool mode, 20 max client connections per worker, 5 default pool size per user. All application services (ingestor, feature-worker × N, signal-worker, outcome-worker, celery-beat, cli) connect to `pgbouncer:6432` instead of `postgres:5432`. Alembic migrations **and** the one-shot features backfill tool (§10.2) connect directly to `postgres:5432`, bypassing pgbouncer — migrations use statements pgbouncer's session pool doesn't support, and the bulk-write backfill would hog pool connections for the duration of the run.

### 9.3 Feature-worker horizontal scaling
`docker-compose.yml` defines `feature-worker` with `deploy.replicas: 4` (tunable). Celery's default round-robin consumption from the `features` queue load-balances across them. Each replica exposes its own `/metrics` endpoint on a distinct port (9101, 9104, 9105, 9106 — the Phase 0 hardcoded port scheme moves into a small port-allocator helper).

### 9.4 Backfill Celery Beat job
Phase 0 ships `expected_candle_opens` helper. Phase 1 wires it:
- Beat job `ingestor.backfill.scan_gaps` runs every 5 minutes.
- For each (symbol, timeframe) in the universe, compute expected opens for the last 6 hours; find missing opens in `raw_candles`; enqueue a `backfill_candles` task per gap with the REST endpoint to fill from.
- `backfill_candles` Celery task fetches via Binance REST `GET /fapi/v1/klines`, inserts with `on_conflict_do_nothing`, dispatches `compute_features` for each newly-inserted close.

### 9.5 `raw_candles` partitioning
Declarative partitioning by month on `open_time`. New migration creates the partitioned table; partition-create beat job runs daily and ensures next-month and current-month partitions exist a week ahead of time. Retention remains unbounded — Phase 1 does not delete old partitions.

### 9.6 Celery Beat persistence
`celery-redbeat` replaces the in-memory scheduler so 3d/7d countdowns survive beat restarts. Redis db 2 used for beat state (distinct from broker db 0 and result backend db 1).

### 9.7 Prometheus scrape config
Adds pgbouncer metrics via `pgbouncer_exporter` sidecar. Adds 4 feature-worker scrape targets instead of 1. No change to Grafana dashboard other than relabeling.

---

## 10. Feature-version + backfill

### 10.1 feature-version

`features.feature_version` column (Text) already exists in Phase 0 with the git-SHA. Phase 1's feature-worker writes the Phase 1 git-SHA at deploy. Rows with Phase 0 SHA are considered stale.

### 10.2 One-shot backfill at Phase 1 deploy

`docker compose run --rm tools python -m trading_sandwich.features.backfill` does:

1. Read `raw_candles` table in batches of 10k candles, ordered by `(symbol, timeframe, open_time)`.
2. For each candle, compute the full Phase 1 indicator set (using the same compute function the live worker uses).
3. Upsert into `features` with `on_conflict_do_update`, overwriting Phase 0 rows.
4. Respect the minimum-history rule: candles that don't have `max(required_lookback)` bars of prior raw data get skipped (not written).
5. Emit Prometheus counter `ts_backfill_features_written_total` for observability.

This is a one-shot tool, not a long-lived service.

**Deploy runbook for Phase 1:**

1. Merge + build images.
2. Start only `postgres` + `pgbouncer` + `redis`.
3. `alembic upgrade head` — applies migrations 0003–0009.
4. **REST backfill of raw candles** (new tool): `docker compose run --rm tools python -m trading_sandwich.ingestor.rest_backfill --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT --timeframes 5m,15m,1h,4h,1d --days 365`. Fetches 1 year of klines per (symbol, TF) via Binance REST and inserts into `raw_candles`. Needed because EMA-200 on 1d requires 200 days of history before the first valid row; Phase 0's days-of-raw-data would yield zero backfilled 1d features without this step.
5. **Features backfill:** `docker compose run --rm tools python -m trading_sandwich.features.backfill`. Populates Phase 1 `features` rows for every raw candle with sufficient lookback.
6. Populate microstructure tables: REST beat jobs will fill funding / OI / LSR from the moment beat starts, but `ingestor/rest_backfill_microstructure.py` is run once to pull the last 30 days of funding rates and 7 days of OI history so regime calculations that depend on funding-24h-mean etc. are meaningful from t=0.
7. Start full stack: `docker compose up -d`.
8. Monitor Grafana + CLI `stats` for 1 hour; exit criteria §14.

---

## 11. Data flow (unchanged vs Phase 0, sizes updated)

```
Binance WS (OHLCV + depth)  ─→  ingestor  ─→  raw_candles                 ─┐
Binance WS (depth)          ─→  ingestor  ─→  raw_orderbook_snapshots      │
Binance REST (funding)      ─→  Beat job  ─→  raw_funding                  │
Binance REST (OI)           ─→  Beat job  ─→  raw_open_interest            │
Binance REST (L/S ratio)    ─→  Beat job  ─→  raw_long_short_ratio         │
                                                                           │
                                           ↓ compute_features              │
                                     features  ←──────────────────────────┘
                                           ↓ detect_signals
                                     signals (gated)
                                           ↓ schedule_outcomes × 6
                                     signal_outcomes
```

Grafana dashboard gets new panels: per-archetype signal rate, per-regime signal distribution, backfill completeness (% of expected candles present in last 24h), pgbouncer pool saturation.

---

## 12. Schema changes

All via Alembic migrations, numbered `0003_…` onward.

- `0003_archetype_check.py` — adds a check constraint on `signals.archetype` with the full 8-archetype list. If this is infeasible (we want `archetype` flexible), drop this migration and rely on Pydantic validation only.
- `0004_raw_orderbook_snapshots.py` — new table. Columns: `symbol`, `captured_at`, `bids` (JSONB array of [price, size]), `asks` (JSONB array). Primary key `(symbol, captured_at)`.
- `0005_raw_funding.py` — new table. Columns: `symbol`, `settlement_time`, `rate` (Numeric). PK `(symbol, settlement_time)`.
- `0006_raw_open_interest.py` — `symbol`, `captured_at`, `open_interest_usd` (Numeric). PK `(symbol, captured_at)`.
- `0007_raw_long_short_ratio.py` — `symbol`, `captured_at`, `ratio` (Numeric). PK `(symbol, captured_at)`.
- `0008_features_extended_columns.py` — adds ~35 new Numeric columns to `features` for the full indicator stack. All nullable. Migration tolerates existing rows.
- `0009_raw_candles_partition.py` — converts `raw_candles` to a declaratively-partitioned table (by month on `open_time`). Uses `pg_partman` or hand-rolled ATTACH PARTITION. Data move via `INSERT … SELECT` into the new partitioned parent, then table-rename swap to minimise downtime (a few seconds).

---

## 13. Boundaries (explicitly NOT in Phase 1)

- ❌ No Claude integration. `claude_decisions` table remains empty. (→ Phase 2)
- ❌ No execution, no paper orders, no testnet orders, no live orders. `trading_enabled: false` remains in `policy.yaml`. (→ Phase 3 paper, Phase 4 live)
- ❌ No ML regime classifier. Rule-based only. Schema permits a swap later without migration. (→ Phase N, after dataset is large enough)
- ❌ No universe auto-expansion or delisting detection. Adding/removing a symbol is a manual `policy.yaml` edit + worker restart. (→ Phase 1.5+)
- ❌ No cold-storage offload. Everything stays hot in Postgres. Partitioning is in-place for future retention, but no partition drops run. (→ Phase 1.5+)
- ❌ No multi-venue. Binance USD-M futures only. (→ Phase 4+ if ever)
- ❌ No PnL tracking, no portfolio state, no position manager. No positions exist. (→ Phase 3)
- ❌ No backtest harness. Outcomes measured forward-only on live data. (→ Phase 2+)
- ❌ No UI, no custom dashboards beyond the provisioned Grafana.

---

## 14. Success criteria (exit criteria for Phase 1)

1. `docker compose up -d` boots postgres + pgbouncer + redis + ingestor + 4 feature-worker replicas + signal-worker + outcome-worker + celery-beat + prometheus + grafana — all green.
2. Ingestor streams 8 symbols × 5 timeframes of OHLCV into `raw_candles` with zero gaps for 1 hour of runtime (verified by the backfill gap-scan job reporting 100% completeness).
3. L2 depth ingestor populates `raw_orderbook_snapshots` at ≥5 rows/sec per symbol.
4. REST beat jobs populate `raw_funding`, `raw_open_interest`, `raw_long_short_ratio` with fresh values within their expected cadence.
5. Feature-worker writes `features` rows with all ~35 new indicator columns populated within expected latency: p95 < 500ms per row (measured via `ts_feature_compute_seconds` histogram).
6. Regime classifier produces valid `trend_regime` and `vol_regime` for every `features` row.
7. All 8 archetypes are wired; each has ≥1 unit test that fires on a crafted input and ≥1 that doesn't fire when the regime gate blocks it.
8. Dedup gate correctly suppresses lower-timeframe signals when a higher-timeframe signal is recent; verified by an integration test with both a 5m and 1h signal for the same symbol+direction within the dedup window.
9. Outcome worker schedules all 6 horizons per `claude_triaged` signal; 15m + 1h outcomes verifiable within test timeframe, 4h+/24h+/3d+/7d+ verified via beat-persistence test.
10. Grafana dashboard shows per-archetype signal rates, per-regime distribution, backfill completeness, and pgbouncer pool saturation.
11. `pytest` green in CI and locally (Phase 0's test suite stays green; Phase 1's new tests added).
12. Zero unhandled exceptions for 24 hours of live runtime against Binance production WS.

---

## 15. Non-goals (anti-scope, for emphasis)

- We are not trying to be profitable in Phase 1. Phase 1 produces signals; no orders are placed. Profit is a Phase 4 concern.
- We are not trying to be the fastest signal emitter. p95 of 500ms per feature row is fine; sub-50ms is Phase N+.
- We are not tuning thresholds yet. `policy.yaml` ships with reasonable defaults; threshold tuning happens once you have 2+ weeks of live data in Grafana.
- We are not backtesting. All outcome data comes from forward measurement on live candles.

---

## 16. Column names added to `features` (migration 0008)

Decided here, not deferred. All columns Numeric, all nullable. 48 new columns.

`ema_8`, `ema_21`, `ema_55`, `ema_200`, `macd_line`, `macd_signal`, `macd_hist`, `adx_14`, `di_plus_14`, `di_minus_14`, `stoch_rsi_k`, `stoch_rsi_d`, `roc_10`, `bb_upper`, `bb_middle`, `bb_lower`, `bb_width`, `keltner_upper`, `keltner_middle`, `keltner_lower`, `donchian_upper`, `donchian_middle`, `donchian_lower`, `obv`, `vwap`, `volume_zscore_20`, `mfi_14`, `swing_high_5`, `swing_low_5`, `pivot_p`, `pivot_r1`, `pivot_r2`, `pivot_s1`, `pivot_s2`, `prior_day_high`, `prior_day_low`, `prior_week_high`, `prior_week_low`, `funding_rate`, `funding_rate_24h_mean`, `open_interest_usd`, `oi_delta_1h`, `oi_delta_24h`, `long_short_ratio`, `ob_imbalance_05`, `ema_21_slope_bps`, `atr_percentile_100`, `bb_width_percentile_100`.

Phase 0's existing `ema_21`, `rsi_14`, `atr_14` columns are retained (not duplicated). `ema_21` is shared between Phase 0 and Phase 1. The Phase 1 migration adds only net-new columns.

Per-symbol funding thresholds beyond the 8-symbol starter set are added to `policy.yaml` as the universe grows. Grafana dashboard JSON panel additions are produced during the plan's Grafana task, not speced here at panel granularity.

---

## 17. Implementation approach (not the plan, just the sketch)

Plan (to be written next via `superpowers:writing-plans`) will be ~40–50 TDD tasks grouped roughly:

1. Deps + infra (TA-Lib, pgbouncer, redbeat, feature-worker scaling, ingestor depth path).
2. Schema + migrations (0003–0009).
3. New raw-data ingestors (funding/OI/LSR REST beat jobs; L2 depth WS).
4. Indicator implementations (one function per indicator group, unit-tested).
5. Regime classifier + unit tests.
6. All 8 detectors + unit tests per detector + regime-gate tests.
7. Dedup gate + unit + integration tests.
8. Outcome worker horizon expansion + redbeat persistence test.
9. Backfill one-shot tool + test.
10. Grafana dashboard additions.
11. End-to-end integration test covering the full Phase 1 chain on a crafted pattern that fires at least one archetype from each regime.
12. Runbook update in README for Phase 1 deploy (migrate → backfill → up).

Checkpoints: after infra (1), after schema (2), after indicators (4), after detectors (6), after outcomes (8), after E2E (11).

---

