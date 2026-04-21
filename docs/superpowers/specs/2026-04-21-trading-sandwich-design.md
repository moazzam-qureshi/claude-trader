# Trading Sandwich — Design Spec

**Date:** 2026-04-21
**Pattern:** Instance of MCP-Sandwich (see `architecture.md` at repo root)
**Status:** Approved for implementation planning

---

## 1. System Overview

### Purpose

A continuously-running Python system that ingests live Binance market data (candles, trades, funding, open interest), computes a technical-indicator feature substrate on every candle close, detects rule-based signals against a library of archetypes, and tracks the forward outcome of every signal at six horizons. High-confidence signals trigger Claude via the CLI; Claude reasons over the signal using MCP tools that expose the feature store, signal history, and — critically — similar past signals *with their realized outcomes*. Claude decides whether to alert, paper-trade, or place a live order (under policy rails). Binance and TradingView MCPs are also available to Claude for cross-checks and execution read-paths.

### What makes it compound

Every signal is logged with its feature snapshot; every signal gets its forward outcome attached. Within weeks, Claude's `find_similar_signals` tool returns grounded context: *"this archetype fired 34 times in similar regimes; median 4h return +0.8%, but MAE averaged −1.2·ATR."* Within months, the features + outcomes table is directly consumable for ML training. The data moat is a side effect of correct ingestion discipline — no extra work, just "don't delete raw."

### Scope (v1)

**In scope:**
- Engine: ingestion, feature computation, signal detection, outcome tracking
- Custom MCP server exposing typed tools to Claude
- CLI (single Claude-invocation path)
- CLAUDE.md and `policy.yaml` as versioned policy
- Postgres + pgvector as durable store
- Discord alerts
- Paper, testnet, and live execution modes (with hard safety rails)
- Weekly retrospection loop (Layer 3 learning)

**Out of scope for v1:**
- ML model training/inference (data collection starts day one; training is a later phase)
- Dashboard UI (CLI is sufficient; defer until friction proves it)
- Order-book depth ingestion (phase 2)
- Multi-exchange (CCXT Pro makes this a later config addition)
- Cross-domain memory (only relevant if a second MCP-Sandwich app exists)

### Success criteria

1. Engine runs continuously for 7+ days without human intervention (watchdog restarts acceptable).
2. Every signal emitted has all 6 forward outcomes attached within 7 days of firing.
3. Claude invocation rate stays under the daily cap without manual tuning after the first week.
4. `find_similar_signals` returns meaningful grounded context (≥10 comparable historical signals) for common archetypes within 30 days of uptime.
5. Zero raw-data loss: gap detection confirms no dropped candles/trades/funding rows.
6. In live mode: zero orders submitted without a linked `claude_decisions` row, zero orders without attached stop-loss, zero policy-rail bypasses.

---

## 2. Architecture & Services

### Shape

Single `docker-compose.yml`, all Python, Postgres as the only cross-service durable contract, Redis as the Celery broker. The application splits into **five long-lived Python workers** (ingestor + feature + signal + outcome + execution) plus `celery-beat`, `mcp-server`, Postgres, Redis, and the observability pair (Prometheus, Grafana). CLI is oneshot via `docker compose run --rm cli`.

```
┌──────────────────────────────────────────────────────────────────┐
│  TOP — Alert surfaces                                            │
│    Discord webhook (signals + health), CLI stdout, future UI     │
└──────────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────────┐
│  MIDDLE — Claude Code (invoked via CLI only)                     │
│    CLAUDE.md: triage rubric, voice, tool conventions             │
│    MCPs: custom trading-mcp + Binance MCP + TradingView MCP      │
└──────────────────────────────────────────────────────────────────┘
                              ▲ MCP
┌──────────────────────────────────────────────────────────────────┐
│  BOTTOM — Data + analysis layer (all Python, Postgres contract)  │
│                                                                   │
│   ingestor ──► raw_candles, raw_trades, funding, open_interest   │
│                              │                                    │
│                              ▼                                    │
│   feature-worker ──────► features (one row per symbol×tf×close)  │
│                              │                                    │
│                              ▼                                    │
│   signal-worker ───────► signals (archetype detections + gating) │
│                              │                                    │
│                              ▼                                    │
│   outcome-worker ──────► outcomes (forward measurements ×6)      │
│                              │                                    │
│   execution-worker ─────► orders, order_modifications (live)     │
│                              │                                    │
│   mcp-server ◄───────────────┘ reads all tables, exposes tools   │
└──────────────────────────────────────────────────────────────────┘
```

### Services

| Service | Lifecycle | Responsibility |
|---|---|---|
| `postgres` | long-lived | Durable store (Postgres 16 + pgvector) |
| `redis` | long-lived | Celery broker + pub/sub |
| `ingestor` | long-lived | CCXT Pro WS streams (candles, trades, funding) + REST (OI, backfill). Writes raw tables. Never imports feature code. |
| `feature-worker` | long-lived | Celery consumer (queue `features`). Computes indicator stack + derived features on each candle close. |
| `signal-worker` | long-lived | Celery consumer (queue `signals`). Runs archetype detectors, applies gating, enqueues triage + outcome jobs. |
| `outcome-worker` | long-lived | Celery consumer (queue `outcomes`). Processes scheduled forward-measurement jobs. |
| `execution-worker` | long-lived | Celery consumer (queue `execution`). Handles Binance order submission, modification, reconciliation. |
| `celery-beat` | long-lived | Scheduled tasks: outcome measurements, daily cap reset, weekly retrospection, health probes, gap scans, position watchdog. |
| `mcp-server` | long-lived | FastMCP server exposing tools over the DB. Imports feature/detector modules directly — no drift. |
| `prometheus` | long-lived | Metrics scrape |
| `grafana` | long-lived | Dashboards |
| `cli` | oneshot | `docker compose run --rm cli <cmd>`. Spawns `claude -p` for Claude-invoking commands; DB-direct for observability/mutations. |

### Language and standardized components

**Single language: Python 3.12+.** One repo, one `pyproject.toml`, one test suite.

| Concern | Component |
|---|---|
| Durable store | Postgres 16 + pgvector |
| Schema migrations | Alembic |
| ORM / DB access | SQLAlchemy 2.0 + asyncpg |
| Task queue | Celery + Redis broker |
| Scheduler | Celery Beat |
| Exchange connectivity | CCXT Pro (async WS + REST) |
| Indicators | `pandas-ta` (primary) + TA-Lib (selective, for Wilder-RSI / ADX) |
| Data validation | Pydantic v2 |
| MCP server | Official `mcp` Python SDK (FastMCP) |
| CLI | Typer |
| Config | pydantic-settings |
| Logging | structlog (JSON) |
| Metrics | Prometheus client + Grafana |
| Error tracking | Sentry |
| Testing | pytest + pytest-asyncio + testcontainers |
| CI | GitHub Actions |

### The three protocols (architecture §2)

- **DB wire** — every cross-service state transfer goes through Postgres. No inter-service HTTP, no shared files.
- **MCP** — only channel between Claude and data/action tools.
- **HTTP** — only for external services (Binance, TradingView, Discord).

Celery uses Redis as its message broker; that is an internal implementation detail of the queue, not a fourth protocol.

### Concurrency safety

- **Postgres advisory locks** keyed on `(command_name, signal_id)` prevent double-triage across Celery retries.
- **Celery acks-late + idempotent handlers** — every task handler upserts on natural keys so re-delivery is safe.
- **Kill-switch** is a global flag in `policy.yaml`, checked synchronously before every order submission.

### Volumes

- Named volume for Postgres data (with WAL archiving for PITR).
- Named volume for Claude Code OAuth token (portable per architecture §4).
- Bind-mount workspace directory so CLAUDE.md edits hot-reload on next invocation.

---

## 3. Data Model

Postgres, Alembic-managed, SQLAlchemy 2.0 async models. Raw kept forever, features re-derivable, outcomes attached, decisions fully audit-logged.

### Raw tables (ingestor writes only; never deleted)

```
raw_candles
  symbol                text
  timeframe             text                        (1m, 5m, 15m, 1h, 4h, 1d)
  open_time             timestamptz
  close_time            timestamptz
  open, high, low, close, volume           numeric
  quote_volume, trade_count                numeric
  taker_buy_base, taker_buy_quote          numeric
  ingested_at           timestamptz default now()
  PRIMARY KEY (symbol, timeframe, open_time)

raw_trades
  symbol                text
  trade_id              bigint
  price, qty, quote_qty                    numeric
  is_buyer_maker        boolean
  event_time            timestamptz
  ingested_at           timestamptz default now()
  PRIMARY KEY (symbol, trade_id)
  # Partitioned monthly on event_time (high volume)

raw_funding
  symbol                text
  funding_time          timestamptz
  funding_rate          numeric
  mark_price            numeric
  ingested_at           timestamptz default now()
  PRIMARY KEY (symbol, funding_time)

raw_open_interest
  symbol                text
  snapshot_time         timestamptz
  open_interest         numeric
  open_interest_usd     numeric
  ingested_at           timestamptz default now()
  PRIMARY KEY (symbol, snapshot_time)

ingestion_gaps
  id, symbol, stream_type, gap_start, gap_end,
  detected_at, backfilled_at                timestamptz
```

### Features (wide table, ML-ready; one row per `symbol × timeframe × close_time`)

```
features
  symbol, timeframe, close_time             -- PK
  close_price                                numeric

  -- Trend
  ema_9, ema_21, ema_50, ema_200             numeric
  sma_50, sma_200                             numeric
  ema_stack_bullish                           boolean    (9>21>50>200)
  golden_cross_state                          text       ('golden'|'death'|'neither')
  adx_14, di_plus, di_minus                   numeric
  ichimoku_tenkan, _kijun, _span_a, _span_b, _chikou     numeric
  ichimoku_cloud_state                        text       ('above'|'below'|'inside')

  -- Momentum
  rsi_14                                      numeric
  rsi_divergence                              text       ('bullish'|'bearish'|null)
  macd_line, macd_signal, macd_hist           numeric
  macd_zero_line_state                        text       ('above'|'below')
  stoch_k, stoch_d                            numeric
  roc_10                                      numeric

  -- Volatility
  atr_14                                      numeric
  atr_percentile_90d                          numeric    (0..1)
  bb_upper, bb_mid, bb_lower, bb_pct_b, bb_bandwidth     numeric
  kc_upper, kc_mid, kc_lower                  numeric
  bb_kc_squeeze                               boolean
  realized_vol_24h, realized_vol_7d, realized_vol_30d    numeric

  -- Volume / flow
  obv                                         numeric
  obv_divergence                              text
  vwap_session, vwap_anchored                 numeric
  volume_profile_poc, _vah, _val              numeric
  cvd_24h                                     numeric
  funding_rate_current                        numeric
  funding_zscore_30d                          numeric
  open_interest_current                       numeric
  oi_delta_24h_pct                            numeric

  -- Market structure
  swing_high_last, swing_low_last             numeric
  structure_trend                             text       ('HH_HL'|'LH_LL'|'transition'|'range')
  nearest_resistance, nearest_support         numeric
  dist_to_resistance_atr, dist_to_support_atr            numeric

  -- Regime
  trend_regime                                text       ('trending_up'|'trending_down'|'ranging'|'transition')
  vol_regime                                  text       ('low'|'mid'|'high')
  btc_dominance, eth_btc_ratio                numeric

  -- MTF + confluence
  mtf_alignment_score                         smallint   (0..4)
  confluence_count_at_price                   smallint

  -- Candlestick patterns
  pattern_engulfing_bull, _engulfing_bear, _hammer, _shooting_star,
  pattern_doji, _inside_bar, _pin_bar         boolean

  computed_at                                 timestamptz default now()
  feature_version                             text       (git sha at compute time)
  PRIMARY KEY (symbol, timeframe, close_time)
```

**Indexes:** `(symbol, timeframe, close_time DESC)`, `(trend_regime, vol_regime)`.

### Signals

```
signals
  signal_id             uuid PRIMARY KEY
  symbol, timeframe
  archetype             text      (trend_pullback|squeeze_breakout|divergence|
                                   liquidity_sweep|funding_extreme|range_rejection)
  fired_at              timestamptz
  candle_close_time     timestamptz
  trigger_price         numeric
  direction             text      ('long'|'short')

  confidence            numeric   (0..1)
  confidence_breakdown  jsonb

  gating_outcome        text      ('claude_triaged'|'cooldown_suppressed'|
                                   'dedup_suppressed'|'daily_cap_hit'|'below_threshold')
  features_snapshot     jsonb     (denormalized for audit integrity)

  stop_price, target_price       numeric
  rr_ratio              numeric

  detector_version      text      (git sha)
  created_at            timestamptz default now()

  INDEX (symbol, fired_at DESC)
  INDEX (archetype, fired_at DESC)
  INDEX (gating_outcome, fired_at DESC)
```

### Outcomes (scoreboard)

```
signal_outcomes
  signal_id             uuid references signals
  horizon               text      ('15m'|'1h'|'4h'|'24h'|'3d'|'7d')
  measured_at           timestamptz
  close_price           numeric
  return_pct            numeric
  mfe_pct, mae_pct      numeric
  mfe_in_atr, mae_in_atr            numeric
  stop_hit_1atr, target_hit_2atr    boolean
  time_to_stop_s, time_to_target_s  int nullable
  regime_at_horizon     text
  PRIMARY KEY (signal_id, horizon)
```

### Claude decisions (non-negotiable event log, architecture §6)

```
claude_decisions
  decision_id           uuid PRIMARY KEY
  signal_id             uuid nullable
  invocation_mode       text      ('triage'|'analyze'|'retrospect'|'ad_hoc')
  invoked_at, completed_at        timestamptz
  duration_ms           int
  prompt_version        text      (git rev-parse HEAD)
  input_context         jsonb
  tools_called          jsonb     ([{tool, args, duration_ms}, ...])
  output                jsonb
  decision              text      ('alert'|'paper_trade'|'live_order'|'ignore'|'research_more')
  rationale             text
  error                 text nullable
  cost_tokens_in, cost_tokens_out, cost_tokens_cache     int
```

### Execution tables

```
orders
  order_id              uuid PRIMARY KEY
  client_order_id       text UNIQUE NOT NULL          -- idempotency key
  exchange_order_id     text nullable
  decision_id           uuid references claude_decisions
  signal_id             uuid nullable references signals
  symbol, side, order_type
  size_base, size_usd, limit_price                    numeric
  stop_loss             jsonb                          (StopLossSpec)
  take_profit           jsonb nullable                 (TakeProfitSpec or list)
  status                text      ('pending'|'open'|'partial'|'filled'|'canceled'|'rejected')
  execution_mode        text      ('paper'|'testnet'|'live')
  submitted_at, filled_at, canceled_at                timestamptz
  avg_fill_price, filled_base, fees_usd               numeric
  rejection_reason      text nullable
  policy_version        text                           (git sha of policy.yaml at submission)

order_modifications
  mod_id                uuid PRIMARY KEY
  order_id              uuid references orders
  kind                  text      ('stop_moved'|'tp_moved'|'size_changed'|'canceled')
  old_value, new_value  jsonb
  reason                text
  decision_id           uuid references claude_decisions
  at                    timestamptz

positions              -- materialized from orders + exchange sync
  symbol, side, size_base, avg_entry, unrealized_pnl_usd, opened_at, closed_at nullable

risk_events
  event_id              uuid PRIMARY KEY
  kind                  text      (each policy rule has a canonical name, e.g., 'max_order_usd_exceeded')
  severity              text      ('info'|'warning'|'block'|'kill_switch')
  context               jsonb     (order_request, account_state, rule_config)
  action_taken          text
  at                    timestamptz
```

### Alerts

All trade state (paper, testnet, live) lives in the `orders` + `positions` tables above; `execution_mode` distinguishes them. There is no separate `paper_trades` table — paper orders are `orders where execution_mode='paper'` with a paper adapter that simulates fills against live price feeds.

```
alerts
  alert_id, signal_id, decision_id, channel, sent_at, payload
  UNIQUE (signal_id, channel)

overrides              -- Layer 2 learning (preference capture)
  override_id, decision_id, new_decision, reason, at
```

### Vector table (pgvector — populated lazily from phase 2 onward)

```
signal_embeddings
  signal_id uuid PRIMARY KEY references signals
  embedding vector(1536)
  embedded_at timestamptz
  INDEX USING ivfflat (embedding vector_cosine_ops)
```

### Retention

- `raw_*` — never deleted. `raw_trades` partitioned monthly.
- `features` — never deleted; cheap to keep.
- `signals`, `signal_outcomes`, `claude_decisions`, `orders`, `order_modifications`, `risk_events`, `alerts`, `overrides` — never deleted; they are the moat.
- `ingestion_gaps` — kept for audit.
- `feature_version` and `detector_version` columns let logic evolve without breaking historical comparability.

---

## 4. Analysis Pipeline

### Stage 1 — Ingestion

`ingestor` maintains CCXT Pro subscriptions per (symbol, stream):
- `watchOHLCV(symbol, timeframe)` for universe × timeframes
- `watchTrades(symbol)` per symbol
- `watchFundingRate(symbol)` per perp
- `fetchOpenInterest(symbol)` on a 1-minute timer (REST only on Binance)

On each event:
1. Upsert to the appropriate `raw_*` table (idempotent on PK — WS replays are safe).
2. **On candle close only**: publish `compute_features.delay(symbol, timeframe, close_time)`.

**Reliability:** CCXT Pro handles reconnection with exponential backoff. A Celery Beat task every 5 minutes detects gaps (expected vs. actual candle counts for last hour), writes to `ingestion_gaps`, and spawns `backfill_candles` tasks that use REST. Trades gaps are logged but not REST-backfilled (cost/benefit).

### Stage 2 — Feature computation

`feature-worker` consumes `compute_features(symbol, timeframe, close_time)`:

1. Load rolling window: last 500 candles → pandas DataFrame.
2. Run indicator pipeline. One module per group in `features/compute.py`:
   - `compute_trend(df)` — EMA/SMA/ADX/Ichimoku (pandas-ta + TA-Lib)
   - `compute_momentum(df)` — RSI + divergence, MACD, Stoch, ROC
   - `compute_vol(df)` — ATR + percentile, BB, KC, squeeze, realized vol
   - `compute_volume(df, trades_df, funding_df, oi_df)`
   - `compute_structure(df)` — swings, S/R clusters, HH/HL state
   - `compute_patterns(df)` — candlestick detectors (TA-Lib)
3. Compute derived/composite:
   - `regime` from ADX + EMA stack + vol percentile
   - `mtf_alignment_score` by reading latest features row from sibling timeframes
   - `confluence_count_at_price` (levels within 0.25·ATR of close)
4. Upsert one `features` row (idempotent on PK).
5. Publish `detect_signals.delay(symbol, timeframe, close_time)`.

Every function is pure: `(DataFrame, config) → dict`. Unit-tested with fixtures. `feature_version = git rev-parse HEAD` recorded per row.

### Stage 3 — Signal detection

`signal-worker` consumes `detect_signals(symbol, timeframe, close_time)`:

1. Read the new `features` row + a 20-row lookback buffer.
2. Run each archetype detector in `signals/detectors/`. Each returns `Signal | None`:
   - `detect_trend_pullback`
   - `detect_squeeze_breakout`
   - `detect_divergence`
   - `detect_liquidity_sweep`
   - `detect_funding_extreme`
   - `detect_range_rejection`
3. **Confidence scoring** per archetype:
   - Rule-strength (how cleanly conditions matched)
   - MTF alignment bonus
   - Regime-fit modifier
   - Funding/OI context bonus
4. **Gating pipeline:**
   - Threshold: `confidence ≥ 0.7` (per-archetype configurable)
   - Per-symbol cooldown (archetype-specific window)
   - Per-archetype dedup
   - Daily cap (max 20 Claude **triage** invocations/day; Redis counter, Celery Beat reset at 00:00 UTC). `retrospect`, `analyze`, and `ad_hoc` invocations are human-initiated and do **not** count against the cap.
   Every signal is written regardless of gating outcome. `gating_outcome` column records why.
5. If gating passes → `triage_signal.delay(signal_id)` on the `triage` queue.
6. Regardless of gating → schedule six outcome measurements:
   ```python
   for horizon, seconds in HORIZONS.items():
       measure_outcome.apply_async(args=[signal_id, horizon], countdown=seconds)
   ```

### Stage 4 — Outcome measurement

`outcome-worker` consumes `measure_outcome(signal_id, horizon)`:

1. Load signal + ATR at fire time.
2. Load candles from `fired_at` to `fired_at + horizon` (retry with backoff if data not yet available).
3. Compute and upsert: `close_price`, `return_pct`, `mfe_pct`, `mae_pct`, `mfe_in_atr`, `mae_in_atr`, `stop_hit_1atr`, `target_hit_2atr`, `time_to_stop_s`, `time_to_target_s`, `regime_at_horizon`.

Idempotent on `(signal_id, horizon)`.

### Stage 5 — Claude triage

`triage_signal(signal_id)` handler (separate `triage` queue so backpressure cannot block signal detection):

1. Acquire Postgres advisory lock on `('triage', signal_id)`.
2. Invoke the single canonical `invoke_claude(mode='triage', context={'signal_id': ...})`:
   - Validate preconditions (DB, MCP, CLAUDE.md present)
   - Capture `git rev-parse HEAD`
   - Spawn `claude -p "triage <signal_id>"` with `cwd=<workspace>`, 90s timeout
   - Parse structured JSON: `{decision, rationale, alert_payload?, order_request?, research_notes?}`
   - Write `claude_decisions` row with full tool-call trace
3. Act on decision. **Claude performs actions by calling MCP tools during the triage session** (e.g., `save_decision`, `send_alert`, `place_order`). The triage handler does not dispatch on a decision string after the fact — Claude is already inside the tool loop when triage runs. The handler's post-return responsibilities are narrow:
   - Reconcile: verify a `claude_decisions` row was written (Claude should have called `save_decision`); if not, write a fallback row with `decision='ignore'` and `error='no_decision_recorded'`.
   - Release the advisory lock.
   - Log duration + token costs to the existing `claude_decisions` row.

   Valid `decision` values written by `save_decision`: `'alert'`, `'paper_trade'` (synonym for placing an order with `execution_mode=paper` — retained for decision-intent tagging), `'live_order'` (placed an order in the currently-configured mode), `'ignore'`, `'research_more'` (a future job was enqueued).

### Stage 6 — Execution (`execution-worker`)

`place_order` MCP tool does not talk to Binance directly from the MCP process. It writes an `orders` row with `status='pending'` and enqueues `submit_order(order_id)` on the `execution` queue. The `execution-worker` picks it up, runs the pre-trade policy check, and submits to the appropriate adapter (paper/testnet/live).

Policy-check-before-submit runs in the worker, not the MCP, so that policy changes don't require MCP restarts and every submission path — MCP, CLI, future UI — passes through the same gate.

**Pre-trade policy check** (`evaluate_policy(order_request, account_state, policy) → Allow | Block(reason)`):

1. Global kill-switch (`trading_enabled`)
2. `max_order_usd`
3. `max_open_positions_per_symbol` (v1: 1)
4. `max_open_positions_total` (v1: 3)
5. `max_daily_realized_loss_usd` → trip engages kill-switch; manual resume
6. `max_orders_per_day`
7. Per-symbol cooldown after loss (with `override_reason` escape hatch, logged)
8. Stop-loss mandatory on every order
9. Stop-loss sanity band (`min_stop_distance_atr`, `max_stop_distance_atr`)
10. `max_leverage` (v1: 2)
11. Correlated-exposure cap
12. Symbol allowlist

Every block writes a `risk_events` row. Every allow records `policy_version`.

### Stage 7 — Position watchdog (Celery Beat, every 60s)

- Sync open orders/positions from exchange against local tables. Drift → Discord alert.
- Verify every open position has an attached stop on the exchange. Missing → re-submit or flatten.
- Recompute unrealized P&L; drawdown breach → auto kill-switch + optional flatten.

### Stage 8 — Retrospection (Celery Beat, weekly Sunday 03:00 UTC)

`retrospect_week()`:
1. Summarize: signals per archetype, triage rate, alert rate, paper/live P&L, calibration.
2. Invoke Claude in `retrospect` mode with summary + samples.
3. Claude proposes CLAUDE.md / policy.yaml diffs → writes `proposed_changes/YYYY-MM-DD-retrospect.md`.
4. Human review, commit, redeploy.

### Contracts between stages (Pydantic, versioned)

Shared `trading_sandwich.contracts` package imported by every worker:
- `FeaturesRow`
- `Signal`, `GatingDecision`
- `ClaudeInvocation`, `ClaudeResponse`
- `Outcome`
- `OrderRequest`, `StopLossSpec`, `TakeProfitSpec`, `OrderReceipt`
- `PolicyDecision`

Schema change breaks type-checking before it breaks production.

---

## 5. MCP Server & Tool Surface

**Implementation:** official `mcp` Python SDK (FastMCP). Each tool = one decorated async function with typed Pydantic I/O. Stateless (no caches, no session; every call is a fresh DB read). Imports `features.compute` and `signals.detectors` so tool output cannot drift from worker output.

### Market-state reads

- `get_market_snapshot(symbol) → MarketSnapshot` — features across all timeframes, regime, nearest S/R, MTF alignment, confluence, funding, OI 24h delta, last 5 candles per timeframe. **Rich read.**
- `get_feature_history(symbol, timeframe, indicator, lookback) → TimeSeries`
- `get_correlation_matrix(symbols, lookback) → CorrelationMatrix`
- `get_regime_context(symbol?) → RegimeContext`

### Signal reads

- `get_signal(signal_id) → SignalDetail` — signal + features snapshot + measured outcomes + top 5 similar.
- `get_active_signals(filters) → list[SignalSummary]`
- `find_similar_signals(signal_id, k=20, filters?) → list[SimilarSignal]` — **the killer tool.** Structural match (archetype + regime bucket + confidence bucket); augmented by pgvector when available. Returns signals with full realized outcomes.
- `get_archetype_stats(archetype, lookback_days, filters?) → ArchetypeStats`

### Decision + action writes (audit-safe, idempotent)

- `save_decision(signal_id, decision, rationale, alert_payload?, order_request?) → DecisionId`
- `send_alert(channel, payload) → AlertId` — UNIQUE(signal_id, channel) prevents double-send.
- `place_order(decision_id, symbol, side, order_type, size_usd, limit_price?, stop_loss, take_profit?, reduce_only, client_order_id) → OrderReceipt`
- `modify_stop_loss(order_id, new_stop: StopLossSpec) → OrderReceipt`
- `cancel_order(order_id, reason) → CancelReceipt`
- `close_position(symbol, reason) → CloseReceipt`

### Execution reads

- `get_open_orders() → list[OrderSummary]`
- `get_positions() → list[Position]`
- `get_account_state() → AccountState` — equity, free margin, leverage, realized-loss-today.

### Retrospection + intelligence

- `get_recent_outcomes(lookback_days, group_by?) → OutcomeSummary`
- `get_calibration(archetype?, lookback_days) → CalibrationReport`
- `propose_policy_change(summary, proposed_diff, evidence) → ProposalId` — writes markdown to `proposed_changes/` in the workspace (git-reviewable, not DB-bound).

### Utility

- `get_levels(symbol, timeframe) → LevelSet`
- `get_recent_candles(symbol, timeframe, n) → list[Candle]`

### Stop-loss & take-profit specs

```python
class StopLossSpec(BaseModel):
    kind: Literal["fixed_price", "atr_multiple", "percent", "structural"]
    value: Decimal
    trigger: Literal["last", "mark", "index"] = "mark"
    working_type: Literal["stop_market", "stop_limit"] = "stop_market"

class TakeProfitSpec(BaseModel):
    kind: Literal["fixed_price", "rr_ratio", "atr_multiple", "structural"]
    value: Decimal
```

Multi-TP supported via list. `place_order` rejects without `stop_loss`.

### External MCPs (already working, orthogonal)

- **Binance MCP** — account, positions read; order placement is proxied through our `place_order` tool so every order flows through our policy rails and logs.
- **TradingView MCP** — chart/indicator cross-checks.

### CLAUDE.md tool-use conventions

1. Start with `get_market_snapshot` for triage.
2. Always call `find_similar_signals` before finalizing a decision.
3. Always call `get_account_state` before sizing an order.
4. Always include `reason` on `cancel_order` / `modify_stop_loss`.
5. Never modify a stop looser than original.
6. Cross-check with TradingView for chart context or Binance MCP for order-book state as needed.
7. Save decision first, then act.

### Deliberately NOT included (YAGNI)

- No `compute_indicator` tool (features already in table).
- No `backtest` tool in v1 (get_archetype_stats + find_similar_signals cover it with real data).
- No `retrain_model` / `predict` tools (no ML in v1).
- No generic `run_sql` (breaks typed-tool contract).

---

## 6. Operations, Learning Loop, and Phasing

### Observability

- **structlog** JSON to stdout on every worker.
- **Prometheus** scrapes `/metrics` on each service. Canonical metrics per worker described in design conversation. Single pane of glass: Celery queue depths.
- **Grafana** pre-built "Trading Sandwich Health" dashboard: one panel per worker, Celery queues, policy trips, outcomes-pending.
- **Sentry** on every worker with `service`/`symbol`/`timeframe`/`signal_id` tags.

### Health via the same output channels (architecture §8)

No separate monitoring stack. Discord webhook carries:
- **Daily summary** (09:00 local): ingestion uptime, signals fired, triaged, alerts, trades, P&L, policy trips.
- **Paging alerts** (Celery Beat every 5m): WS disconnect >2m, queue depth > threshold, outcome lag > horizon, exception rate >N/min, daily loss cap 80% approached.
- **Kill-switch events** — immediate, red embed.

### Auth, secrets, deployment

- `myapp auth login` for Claude Code OAuth (container-local volume, not host `~/.claude/`).
- Binance API keys via `pydantic-settings`; production uses Docker secrets.
- Workspace bind-mounted; CLAUDE.md hot-reloads.
- Updates: `git pull && docker compose up -d --build`.

### Backup & durability

- Nightly `pg_dump` → S3, encrypted; retention 30 daily / 12 monthly / forever yearly.
- WAL archiving enabled for PITR.
- CLAUDE.md + `policy.yaml` in git — no DB backup needed.
- `proposed_changes/` committed periodically.

### Learning loop (architecture §6 mapped)

| Layer | Ship | Mechanism |
|---|---|---|
| 1. Outcome feedback | Day one | `signal_outcomes` + `find_similar_signals` tool grounds every Claude triage in history |
| 2. Preference learning | ~50 triages | `myapp override <decision_id> <new> --reason` CLI; weekly retrospection ingests overrides and proposes CLAUDE.md edits |
| 3. Prompt evolution | ~100 outcomes | Weekly `retrospect` Celery Beat → `proposed_changes/` markdown → human commit |
| 4. ML models | ~3 months | Separate `ml/` package reading `features` + `signal_outcomes`; new `model_score_v1` feature column + `get_model_score(signal_id)` tool; models versioned by git sha + training snapshot hash |
| 5. Cross-domain memory | If app #2 exists | Shared `~/memory` directory (out of scope for this project) |

### Phasing

| Phase | Duration | Scope |
|---|---|---|
| **0. Skeleton** | 1–2 weeks | Compose up on laptop. Postgres/Redis/Prometheus/Grafana. 2 symbols, minimal indicators (EMA/RSI/ATR), 1 archetype. No Claude. Prove end-to-end data flow. |
| **1. Live ingestion + full indicators** | 1–2 weeks | 5-symbol × 5-timeframe universe. Full indicator stack. All 6 archetypes. Outcome measurement. No Claude. Eyeball signal quality in SQL. |
| **2. MCP server + Claude triage (paper)** | 1 week build + 2–4 week soak | Stand up MCP server. Triage queue wired. `execution_mode=paper`. Accumulate `claude_decisions`. |
| **3. Testnet execution** | 1 week + 1–2 week soak | `execution_mode=testnet`. Full policy rails exercised. Position watchdog running. Zero reconciliation drift required to advance. |
| **4. Live with tight caps** | Indefinite | `execution_mode=live`. Half of planned caps initially. Scale up over weeks. Retrospection runs weekly. |
| **5+. Extensions** | Later | Depth snapshots, TradingView integration, ML models, dashboard, second exchange |

### Testing strategy

- **Unit** — every indicator function (pandas-ta wrappers + our logic); every archetype detector with synthetic fixtures + historical regression fixtures; every policy rule with crafted inputs.
- **Integration** — `testcontainers` (real Postgres + Redis). One end-to-end green path: ingestor → features → signals → outcomes → MCP call. Failure paths: DB down, Redis down, broker error, gap detected.
- **Replay** (phase 2+) — feed historical period through engine, snapshot signal counts / decisions. Drift = bug or intentional `feature_version` bump.
- **Contract tests** on MCP tools — schema match, idempotency.
- **No Binance mocking in integration tests phase 3+** — use testnet. Adapter pattern means the exchange adapter is the one swappable thing.

### CLI commands (v1 surface)

**Claude-invoking** (spawn `claude -p` via the canonical invocation function):
- `myapp triage <signal_id>`
- `myapp analyze <symbol>`
- `myapp retrospect [--weekly]`

**DB-direct** (no Claude):
- `myapp signals [--recent N] [--symbol X] [--archetype Y]`
- `myapp positions`
- `myapp orders [--status X]`
- `myapp pnl [--today|--week|--month]`
- `myapp stats`
- `myapp override <decision_id> <new_decision> --reason "..."`

**Control plane:**
- `myapp trading pause|resume|status`
- `myapp flatten [symbol]` — emergency close all
- `myapp auth login|status`
- `myapp doctor` — full system health check (DB, Redis, MCP, ingestor lag, Celery queues, broker connectivity)

### Policy defaults (v1, in `policy.yaml`)

```yaml
trading_enabled: true
execution_mode: paper              # paper | testnet | live
universe: [BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT]
timeframes: [1m, 5m, 15m, 1h, 4h, 1d]

max_order_usd: 500
max_open_positions_per_symbol: 1
max_open_positions_total: 3
max_orders_per_day: 20
max_daily_realized_loss_usd: 200
max_leverage: 2
max_account_drawdown_pct: 10
max_correlated_usd: 1000

min_stop_distance_atr: 0.3
max_stop_distance_atr: 5.0
default_stop_atr_multiple: 1.5
default_rr_minimum: 1.5

per_archetype_confidence_threshold:
  trend_pullback: 0.70
  squeeze_breakout: 0.72
  divergence: 0.68
  liquidity_sweep: 0.70
  funding_extreme: 0.70
  range_rejection: 0.70

per_archetype_cooldown_minutes:
  trend_pullback: 60
  squeeze_breakout: 30
  divergence: 60
  liquidity_sweep: 30
  funding_extreme: 240
  range_rejection: 60

claude_daily_triage_cap: 20          # triage only; retrospect/analyze/ad_hoc exempt
```

---

## Appendix — Indicator Stack Reference

### Trend
EMA(9, 21, 50, 200), SMA(50, 200), EMA stack order, golden/death cross state, ADX(14) + DI±, Ichimoku (Tenkan, Kijun, Span A/B, Chikou, cloud state).

### Momentum
RSI(14) + divergence detection, MACD(12,26,9) + zero-line state, Stochastic(14,3,3), ROC(10).

### Volatility
ATR(14) + 90d percentile, Bollinger Bands(20, 2σ) + %B + bandwidth, Keltner Channels(20, 2·ATR), BB-inside-KC squeeze, realized volatility (24h / 7d / 30d).

### Volume / Flow
OBV + divergence, VWAP (session + anchored), Volume Profile (POC/VAH/VAL), CVD 24h, funding rate + 30d z-score, open interest + 24h delta.

### Market Structure
Fractal swing high/low, HH/HL / LH/LL sequence, S/R clusters from swings + POCs, distance-to-nearest-level in ATR units.

### Regime
Trend regime (trending up / trending down / ranging / transition), volatility regime (low / mid / high), BTC dominance, ETH/BTC ratio.

### Candlestick patterns (detectors; only meaningful at key levels)
Engulfing (bull/bear), hammer, shooting star, doji, inside bar, pin bar.

### Derived / composite
MTF alignment score, confluence count at price, distance-to-level in ATR.

### Signal archetypes (v1)
- **trend_pullback** — trending regime + retrace to EMA21/50 + momentum reset
- **squeeze_breakout** — BB-inside-KC squeeze + range expansion
- **divergence** — price HH/LL vs RSI or OBV disagreement at key level
- **liquidity_sweep** — wick beyond swing + reclaim
- **funding_extreme** — |funding z-score| > N vs 30d + price at structural level
- **range_rejection** — ranging regime + tag of range extreme + reversal candle
