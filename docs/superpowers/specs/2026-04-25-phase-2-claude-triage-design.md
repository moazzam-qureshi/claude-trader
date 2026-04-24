# Phase 2 — Claude Triage, Approval Loop, and Execution (Paper + Live)

> **Status:** Draft — awaiting user review
> **Author:** Claude (brainstormed 2026-04-25)
> **Predecessors:** `2026-04-21-trading-sandwich-design.md` (Phase 0), `2026-04-24-phase-1-feature-stack.md` (Phase 1)
> **Architecture reference:** `/architecture.md`

---

## 1. Goal

Phase 1 ships a signal generator. Phase 2 wires **Claude Code as the triage
operator** on top of it and adds a **human-approved execution loop** so
high-conviction signals can become real orders.

End-to-end flow after Phase 2:

```
signal passes gating (Phase 1)
        │
        ▼
triage-worker spawns `claude -p "triage <signal_id>"`
        │
        ▼
Claude (reading runtime/CLAUDE.md + runtime/GOALS.md) calls MCP tools:
  get_signal → get_market_snapshot → find_similar_signals
  → get_archetype_stats → save_decision [→ send_alert] [→ propose_trade]
        │
        ▼
claude_decisions row written
        │
        ▼
If proposal: Discord card posted with ✅ / ❌ / 🔎 buttons
        │
        ▼ (operator taps ✅)
discord-listener validates + flips trade_proposals.status = 'approved'
        │
        ▼
execution-worker runs policy rails → submits to paper or live adapter
        │
        ▼
orders row + Binance response (live) or simulated fill (paper)
        │
        ▼
signal_outcomes continue forward measurement (Phase 1 behavior unchanged)
```

Phase 2 ships:

- **Triage loop** — Claude Code spawned via CLI (`claude -p`) per gated signal.
- **MCP server** — long-lived FastMCP service exposing the Stage 1 tool
  surface (reads + decision writes + trade proposal + alert).
- **Runtime policy** — `runtime/CLAUDE.md` rewritten as an elite seasoned-
  veteran discretionary trader prompt, regime-adaptive, ~600–900 lines.
  `runtime/GOALS.md` added as a separate narrative file referenced by
  CLAUDE.md.
- **Approval loop** — Discord bot posting proposal cards with buttons;
  operator tap flips proposal state and enqueues execution.
- **Execution** — full `execution-worker`, paper adapter and live adapter
  (Binance USD-M futures via CCXT Pro), pre-trade policy rails, position
  watchdog, kill-switch. `trading_enabled: false` by default; arming is a
  committed policy change.
- **Daily triage cap** — enforced at the signal-worker gate via date-keyed
  Redis counter.

Phase 2 does **not** ship: weekly retrospection loop (tools land in Stage 2
but the Celery Beat job is Phase 3+), ML, pgvector-backed similar-signal
search, testnet adapter, multi-operator support.

---

## 2. What stays from Phase 0 / Phase 1 (locked decisions)

- Claude invocation is **always** via `claude -p` subprocess. No direct
  Anthropic API, no SDK tool-use loop. The single canonical invocation
  function spawns Claude Code with `cwd=/workspace` and a 90s timeout.
- MCP server uses the official `mcp` Python SDK (FastMCP). Stateless.
- All-Python stack; CCXT Pro for Binance; Celery + Redis; Alembic.
- `runtime/CLAUDE.md` + `policy.yaml` + `runtime/GOALS.md` are git-tracked;
  `git rev-parse HEAD` stored in `claude_decisions.prompt_version` on every
  invocation.
- Postgres is the only durable state channel between services. No inter-
  service HTTP except MCP (Claude ↔ MCP server) and external (Binance,
  Discord).
- Raw data kept forever. Every decision leaves a trace.
- Testcontainers for integration tests.

---

## 3. Phase 2 is built in two stages

### Stage 1 — proof

Minimum surface to run the triage loop end-to-end against real Binance
signals with a paper adapter (and a live adapter available behind
`trading_enabled: false`).

**MCP tools (7):** `get_signal`, `get_market_snapshot`,
`find_similar_signals`, `get_archetype_stats`, `save_decision`,
`send_alert`, `propose_trade`.

**Services added:** `mcp-server`, `triage-worker`, `discord-listener`,
`execution-worker`.

**Tables added (migration 0010):** `trade_proposals`, `orders`,
`order_modifications`, `positions`, `risk_events`, `kill_switch_state`,
`alerts`. Plus indexes on `claude_decisions`.

**Runtime policy written:** `runtime/CLAUDE.md` and `runtime/GOALS.md`.

**CLI added:** `myapp proposals`, `myapp approvals`, `myapp orders`,
`myapp positions`, `myapp trading pause|resume|status`, `myapp calibration`,
`myapp flatten`, `myapp auth login` (Claude Code OAuth).

Exit: all 12 success criteria in §14 met.

### Stage 2 — expansion

Built once Stage 1 proves out. Expands the tool surface so CLAUDE.md has
more to reach for. No new services, no new tables.

**MCP tools added (8):** `get_feature_history`, `get_correlation_matrix`,
`get_regime_context`, `get_levels`, `get_recent_candles`,
`get_recent_outcomes`, `get_calibration`, `propose_policy_change`.

Retrospection Celery Beat job (`retrospect_week`) is **not** built in
Phase 2. The tools exist but the scheduled invocation lands Phase 3+.

---

## 4. Architecture additions

### 4.1 New services

| Service | Image base | Purpose |
|---|---|---|
| `mcp-server` | Python + trading_sandwich | Long-lived FastMCP server. HTTP/SSE transport on internal port `:8765`. Stateless. Imports `features.compute` + `signals.detectors` so tool output cannot drift from worker output. |
| `triage-worker` | Python + Node.js 20 + `@anthropic-ai/claude-code` | Celery worker consuming the `triage` queue. One task: `triage_signal(signal_id)` which spawns `claude -p`. Mounts a named volume at `/root/.claude` for OAuth. |
| `discord-listener` | Python | `discord.py` bot with Gateway connection. Receives button interactions. Validates operator identity. Flips `trade_proposals.status`. Enqueues `submit_order` on approval. |
| `execution-worker` | Python + CCXT Pro | Celery worker consuming the `execution` queue. Tasks: `submit_order`, `modify_stop_loss` (deferred to Phase 3 for modifications beyond initial submission), `cancel_order`, `reconcile_positions`. Loads paper or live adapter based on `execution_mode`. |

Existing services (`postgres`, `pgbouncer`, `redis`, `ingestor`,
`feature-worker × 4`, `signal-worker`, `outcome-worker`, `celery-beat`,
`prometheus`, `grafana`) unchanged.

### 4.2 New Celery queues

- `triage` — consumed only by `triage-worker`.
- `execution` — consumed only by `execution-worker`.

Existing queues (`features`, `signals`, `outcomes`) unchanged.

### 4.3 New workspace contract

Repo root is bind-mounted as `/workspace` into the `triage-worker`
container. The file layout Claude sees:

```
/workspace/
  .mcp.json                   # tells Claude Code to connect to mcp-server
  runtime/
    CLAUDE.md                 # the persona + playbooks
    GOALS.md                  # narrative goals
  policy.yaml                 # the rails
```

`.mcp.json` declares one server:

```json
{
  "mcpServers": {
    "trading": {
      "url": "http://mcp-server:8765/sse",
      "transport": "sse"
    }
  }
}
```

All three of `runtime/CLAUDE.md`, `runtime/GOALS.md`, `policy.yaml`
hot-reload — no container restart on edit, next invocation sees the new
content. Every edit is a git commit (policy discipline); the commit SHA is
captured in `claude_decisions.prompt_version`.

### 4.4 Daily cap Redis key

`claude_triage:{YYYY-MM-DD}` — atomic `INCR`, 48h `EXPIRE` set on first
increment. Signal-worker's gating stage checks `INCR result <= cap`; if over,
`gating_outcome = 'daily_cap_hit'` and the signal is not enqueued. Outcome
scheduling continues unchanged (every signal gets six outcomes regardless
of gating outcome).

No Celery Beat reset task needed. Old keys age out via `EXPIRE`.

---

## 5. Data model

All via Alembic migration `0010_phase2_execution_and_proposals.py`. No
existing column semantics change.

### 5.1 `claude_decisions` — already exists, just indexed

```sql
CREATE INDEX ix_claude_decisions_signal_id ON claude_decisions(signal_id);
CREATE INDEX ix_claude_decisions_invoked_at ON claude_decisions(invoked_at DESC);
CREATE INDEX ix_claude_decisions_decision_invoked_at
  ON claude_decisions(decision, invoked_at DESC);
```

Phase 2 writes `invocation_mode='triage'` and `decision ∈ {alert,
paper_trade, ignore, research_more}`.

**Semantic note on `paper_trade` vs `live_order`.** In Phase 2, the
`decision` string records **Claude's intent**, not the eventual
execution mode. `paper_trade` means "I believe this signal warrants
becoming a trade" and must be accompanied by a `propose_trade` call.
Whether that proposal, once approved, lands on the paper adapter or the
live adapter is determined by `policy.execution_mode` at submit time,
not by Claude. The `decision` vocabulary is therefore slightly
misnamed historically — "paper_trade" is the signal from Claude saying
"propose a trade," and the system decides paper-vs-live downstream.
Renaming the enum value is deferred (would require a data migration for
existing rows and isn't blocking); CLAUDE.md documents the meaning
explicitly.

`live_order` is rejected at the `save_decision` tool layer: Claude
cannot directly request a live execution, only propose a trade.

### 5.2 `trade_proposals` — new

```
trade_proposals
  proposal_id              uuid PRIMARY KEY
  decision_id              uuid NOT NULL UNIQUE REFERENCES claude_decisions
  signal_id                uuid NOT NULL REFERENCES signals
  symbol                   text NOT NULL
  side                     text NOT NULL                -- 'long' | 'short'
  order_type               text NOT NULL                -- 'market' | 'limit' | 'stop'
  size_usd                 numeric NOT NULL
  limit_price              numeric
  stop_loss                jsonb NOT NULL               -- StopLossSpec
  take_profit              jsonb                        -- TakeProfitSpec | null
  time_in_force            text NOT NULL DEFAULT 'GTC'

  opportunity              text NOT NULL                -- ≥80 chars
  risk                     text NOT NULL                -- ≥80 chars
  profit_case              text NOT NULL                -- ≥80 chars
  alignment                text NOT NULL                -- ≥40 chars
  similar_trades_evidence  text NOT NULL                -- ≥80 chars

  expected_rr              numeric NOT NULL
  worst_case_loss_usd      numeric NOT NULL
  similar_signals_count    integer NOT NULL
  similar_signals_win_rate numeric

  status                   text NOT NULL DEFAULT 'pending'
                           -- 'pending' | 'approved' | 'rejected' | 'expired'
                           -- | 'executed' | 'failed'
  proposed_at              timestamptz NOT NULL
  expires_at               timestamptz NOT NULL
  approved_at              timestamptz
  approved_by              text                          -- Discord user id
  rejected_at              timestamptz
  executed_order_id        uuid REFERENCES orders
  policy_version           text NOT NULL                 -- git sha at propose time

  CHECK (length(opportunity) >= 80)
  CHECK (length(risk) >= 80)
  CHECK (length(profit_case) >= 80)
  CHECK (length(alignment) >= 40)
  CHECK (length(similar_trades_evidence) >= 80)
```

`UNIQUE (decision_id)` means one proposal per triage invocation. A new
`claude_decisions` row (re-triage) can create a new proposal; the old one
expires.

State machine (one-way):

```
pending ─► approved ─► executed
       ├► rejected
       ├► expired
       └► failed     (policy-rail block or exchange error after approval)
```

### 5.3 `orders` — new (full Phase 0 schema)

```
orders
  order_id              uuid PRIMARY KEY
  client_order_id       text NOT NULL UNIQUE         -- = proposal_id.hex (idempotency)
  exchange_order_id     text
  decision_id           uuid REFERENCES claude_decisions
  signal_id             uuid REFERENCES signals
  proposal_id           uuid REFERENCES trade_proposals
  symbol                text NOT NULL
  side                  text NOT NULL
  order_type            text NOT NULL
  size_base             numeric
  size_usd              numeric NOT NULL
  limit_price           numeric
  stop_loss             jsonb NOT NULL
  take_profit           jsonb
  status                text NOT NULL
    -- 'pending' | 'open' | 'partial' | 'filled' | 'canceled' | 'rejected'
  execution_mode        text NOT NULL                -- 'paper' | 'live'
  submitted_at          timestamptz
  filled_at             timestamptz
  canceled_at           timestamptz
  avg_fill_price        numeric
  filled_base           numeric
  fees_usd              numeric
  rejection_reason      text
  policy_version        text NOT NULL
```

### 5.4 `order_modifications` — new (write surface deferred to Phase 3)

Schema created; modification MCP tools beyond initial submission land in
Phase 3. The table exists so paper/live fill events can log auto-updates
(e.g., stop filled → `kind='stop_triggered'` row).

```
order_modifications
  mod_id                uuid PRIMARY KEY
  order_id              uuid NOT NULL REFERENCES orders
  kind                  text NOT NULL
  old_value             jsonb
  new_value             jsonb
  reason                text
  decision_id           uuid REFERENCES claude_decisions
  at                    timestamptz NOT NULL
```

### 5.5 `positions` — new (materialized from orders + exchange sync)

```
positions
  symbol                text
  side                  text
  size_base             numeric NOT NULL
  avg_entry             numeric NOT NULL
  unrealized_pnl_usd    numeric
  opened_at             timestamptz NOT NULL
  closed_at             timestamptz
  PRIMARY KEY (symbol, opened_at)
```

Position watchdog upserts on `(symbol, opened_at)`; closes a position by
writing `closed_at`.

### 5.6 `risk_events` — new

```
risk_events
  event_id              uuid PRIMARY KEY
  kind                  text NOT NULL            -- e.g., 'max_order_usd_exceeded'
  severity              text NOT NULL            -- 'info'|'warning'|'block'|'kill_switch'
  context               jsonb NOT NULL           -- order_request, account_state, rule_config
  action_taken          text
  at                    timestamptz NOT NULL
```

### 5.7 `kill_switch_state` — new

```
kill_switch_state
  id                    integer PRIMARY KEY DEFAULT 1   -- singleton row
  active                boolean NOT NULL DEFAULT false
  tripped_at            timestamptz
  tripped_reason        text
  resumed_at            timestamptz
  resumed_ack_reason    text
  CHECK (id = 1)
```

Singleton-row pattern (enforced by `CHECK (id = 1)`). Initialized with
`active=false`. Tripped by writing `active=true` + reason; resumed by
the `myapp trading resume --ack-reason` CLI, which writes
`active=false` + `resumed_ack_reason`. Historical trips stay in the
`risk_events` table; `kill_switch_state` is just the current live
boolean.

### 5.8 `alerts` — new

```
alerts
  alert_id              uuid PRIMARY KEY
  signal_id             uuid REFERENCES signals
  decision_id           uuid REFERENCES claude_decisions
  channel               text NOT NULL
  sent_at               timestamptz NOT NULL
  payload               jsonb NOT NULL
  delivered             boolean NOT NULL DEFAULT false
  error                 text
  UNIQUE (signal_id, channel)
```

The UNIQUE constraint makes `send_alert` idempotent: a second call for the
same `(signal_id, channel)` returns the existing `alert_id` without
re-posting to Discord.

---

## 6. MCP tool surface

FastMCP, stateless, typed Pydantic I/O. Every tool is `async def`
decorated with `@mcp.tool()`.

### 6.1 Stage 1 tools (7)

#### `get_signal(signal_id: UUID) → SignalDetail`

Single-row read. Returns: signal row, `features_snapshot` deserialized,
any `signal_outcomes` already attached (typically only 15m/1h at triage
time), `confidence_breakdown`, `detector_version`.

#### `get_market_snapshot(symbol: str) → MarketSnapshot`

Rich read, one call per symbol. For each timeframe in `policy.yaml`
`universe`, fetches the most recent `features` row. Returns: price,
`trend_regime`, `vol_regime`, EMA stack, ADX, ATR percentile, BB-width
percentile, funding snapshot, OI snapshot, prior-day high/low, prior-week
high/low, distance to nearest pivot in ATR units.

#### `find_similar_signals(signal_id: UUID, k: int = 20) → list[SimilarSignal]`

Pure structural match. Implementation:

1. Load the triggering signal's `(archetype, direction, trend_regime,
   vol_regime, confidence)`.
2. Bucket `confidence` into tertiles (low 0.0–0.33, mid 0.33–0.66,
   high 0.66–1.0).
3. SQL: select `claude_triaged` signals matching `(archetype, direction,
   trend_regime, vol_regime, confidence_bucket)` with at least one
   `signal_outcomes` row attached, ordered by `fired_at DESC`, limit `k`.
4. Join each to all its outcome rows (up to 6 horizons).
5. Return `list[SimilarSignal]` with `match_method: "structural"` and
   `sparse: bool` flag (true if count < k).

Phase 2 does **not** use pgvector. The return contract leaves room for a
future embedding-based variant.

#### `get_archetype_stats(archetype: str, lookback_days: int = 30) → ArchetypeStats`

Aggregate read. Groups by `(archetype, direction, trend_regime,
vol_regime)`. Returns: fire count, median `return_pct` per horizon, win
rate (`return_pct > 0`), median MFE and MAE in ATR units,
`target_hit_2atr` rate, `stop_hit_1atr` rate.

Used when `find_similar_signals` returns < 5 matches.

#### `save_decision(...) → DecisionId`

```python
save_decision(
    signal_id: UUID,
    decision: Literal["alert","paper_trade","ignore","research_more"],
    rationale: str,                  # ≥40 chars
    alert_payload: AlertPayload | None = None,
    notes: str | None = None,
) -> UUID
```

Writes one `claude_decisions` row. Validation:

- `decision == "live_order"` → `ValueError` raised at tool layer.
- `rationale` ≥ 40 chars.
- `alert_payload` required if `decision == "alert"`.

`prompt_version` is captured from an env var set at subprocess spawn time
(the triage-worker runs `git rev-parse HEAD` and passes it in).

Idempotency: `UNIQUE (signal_id, invocation_mode)` added in migration
0010. Re-invocation upserts (last-writer-wins); CLAUDE.md documents that
re-triage supersedes.

Returns the `decision_id` for chaining into `send_alert` /
`propose_trade`.

#### `send_alert(channel: Literal["discord"], payload: AlertPayload) → AlertId`

Writes `alerts` row, then POSTs Discord webhook via `httpx`. The UNIQUE
constraint on `(signal_id, channel)` prevents double-send. Discord webhook
URL is an env var.

In Phase 2 only `discord` is supported. The tool signature allows future
channels without breaking the contract.

#### `propose_trade(...) → ProposalId`

```python
propose_trade(
    decision_id: UUID,

    # Mechanical order params
    symbol: str,
    side: Literal["long","short"],
    order_type: Literal["market","limit","stop"],
    size_usd: Decimal,
    limit_price: Decimal | None,
    stop_loss: StopLossSpec,
    take_profit: TakeProfitSpec | None,
    time_in_force: Literal["GTC","IOC","FOK"] = "GTC",

    # The pitch — all five required, validated length
    opportunity: str,                 # ≥80 chars
    risk: str,                        # ≥80 chars
    profit_case: str,                 # ≥80 chars
    alignment: str,                   # ≥40 chars
    similar_trades_evidence: str,     # ≥80 chars

    # Derived numbers — cross-checked inside the tool
    expected_rr: Decimal,
    worst_case_loss_usd: Decimal,
    similar_signals_count: int,
    similar_signals_win_rate: Decimal | None,
) -> UUID
```

Cross-checks (tool rejects if any fails):

1. `worst_case_loss_usd ≈ size_usd × |entry − stop| / entry` (within 2%).
2. `expected_rr >= policy.default_rr_minimum`.
3. `similar_signals_count` matches a fresh `find_similar_signals(
   signal_id, k=100)` call (sample taken inside the tool; Claude's number
   is verified, not trusted).
4. `decision_id` exists and `claude_decisions.decision == "paper_trade"`.
5. No existing proposal on the same `decision_id` (UNIQUE).
6. `stop_loss.value` is present and within the policy ATR band
   (`min_stop_distance_atr` to `max_stop_distance_atr`).

On success: writes `trade_proposals` row with `status='pending'`,
`expires_at = now() + policy.proposal_ttl_minutes`, `policy_version = git
rev-parse HEAD`. Then posts the Discord proposal card (§7) via the same
`httpx` path as `send_alert`. Returns `proposal_id`.

### 6.2 Stage 2 tools (8) — shape sketched

Built once Stage 1 is proven. Specs for each are short and match Phase 0
§5:

- `get_feature_history(symbol, timeframe, indicator, lookback)` → TimeSeries
- `get_correlation_matrix(symbols, lookback)` → CorrelationMatrix
- `get_regime_context(symbol?)` → RegimeContext
- `get_levels(symbol, timeframe)` → LevelSet (pivots, swings, prior-day/week)
- `get_recent_candles(symbol, timeframe, n)` → list[Candle]
- `get_recent_outcomes(lookback_days, group_by?)` → OutcomeSummary
- `get_calibration(archetype?, lookback_days)` → CalibrationReport
- `propose_policy_change(summary, proposed_diff, evidence)` →
  ProposalId — writes markdown to `proposed_changes/`

### 6.3 Tools deliberately NOT in Phase 2

- `place_order`, `modify_stop_loss`, `cancel_order`, `close_position` —
  execution is gated through the approval loop; Claude cannot bypass it.
- `get_positions`, `get_open_orders`, `get_account_state` — included in
  Stage 2 if needed for context in CLAUDE.md; deferred for the triage
  loop because Phase 2 opens at most one position at a time and the
  triage invocation already carries the signal context.
- Anything with `run_sql` / raw-query semantics — violates typed-tool
  contract.

---

## 7. The approval loop

### 7.1 Proposal card (Discord embed)

When `propose_trade` is called successfully, a Discord embed is posted to
the configured channel:

```
📈 PROPOSAL — BTCUSDT LONG · trend_pullback (1h)
Size $500 · Entry ~$68,420 · Stop $67,150 (1.5·ATR) · TP $71,200 (2.2R)

OPPORTUNITY
<opportunity text, 2–4 sentences>

RISK — worst-case loss $23.50 (4.7% of equity)
<risk text, 2–4 sentences>

PROFIT CASE — expected RR 2.2
<profit_case text, 2–4 sentences>

ALIGNMENT
<alignment text, 1–3 sentences>

EVIDENCE — 14 similar trades · 64% win rate · median +0.9R
<similar_trades_evidence text, 2–5 sentences>

Expires 15:00 · proposal_id <abbreviated>
[✅ Approve]  [❌ Reject]  [🔎 Details]
```

Embed custom IDs encode `proposal_id` + action, e.g., `approve:<uuid>`,
`reject:<uuid>`, `details:<uuid>`.

### 7.2 `discord-listener` behavior

On `on_interaction`:

1. Parse `custom_id` → `(action, proposal_id)`.
2. Check `interaction.user.id == env DISCORD_OPERATOR_ID`. Mismatch →
   ephemeral "not authorized."
3. `SELECT ... FROM trade_proposals WHERE proposal_id = :pid FOR UPDATE`
   in a transaction.
4. Verify `status == 'pending' AND expires_at > now()`. Otherwise → flip
   to `'expired'` if still `pending`, reply ephemerally "expired."
5. For `action == 'approve'`: flip to `'approved'`, set `approved_at`,
   `approved_by`. Commit. Enqueue `submit_order.delay(proposal_id)` on
   the `execution` queue. Edit the original message to remove buttons and
   show "✅ Approved, submitting…".
6. For `action == 'reject'`: flip to `'rejected'`, set `rejected_at`.
   Commit. Edit message to "❌ Rejected."
7. For `action == 'details'`: DM the operator a JSON dump of the full
   proposal + linked `claude_decisions` row. No state change.

The FOR UPDATE row-lock makes double-clicks safe.

### 7.3 Background sweeper

Celery Beat task `expire_stale_proposals` runs every 60 seconds:

```sql
UPDATE trade_proposals
   SET status='expired', rejected_at = now()
 WHERE status='pending' AND expires_at < now()
RETURNING proposal_id;
```

For each expired row, edit the Discord message to remove buttons and
show "⏰ Expired." Prevents dangling "pending" rows from failed listener
interactions.

---

## 8. Execution

### 8.1 `execution-worker`

Celery worker consuming the `execution` queue. Loads an adapter at
startup based on `policy.execution_mode`:

- `paper` → `PaperAdapter` — simulates fills against live candle data
  (reads current candle from `raw_candles`; market orders fill at close,
  limit orders fill when price crosses).
- `live` → `CCXTProAdapter` — real Binance USD-M futures via CCXT Pro.

Both adapters implement the same `ExchangeAdapter` ABC:

```python
class ExchangeAdapter(ABC):
    async def submit_order(self, request: OrderRequest) -> OrderReceipt: ...
    async def cancel_order(self, order_id: str) -> CancelReceipt: ...
    async def get_open_orders(self) -> list[OrderSummary]: ...
    async def get_positions(self) -> list[Position]: ...
    async def get_account_state(self) -> AccountState: ...
```

Adapter is injected in the worker's `celery.on_worker_init` so tests can
swap a `FakeAdapter`.

### 8.2 `submit_order(proposal_id)` task

1. `SELECT ... FOR UPDATE` the proposal; assert `status == 'approved'`.
   Else write `risk_events(kind='submit_order_wrong_status')`, exit.
2. Run **pre-trade policy rails** (§8.3). On any block → flip proposal
   to `'failed'`, write `risk_events(severity='block')`, DM operator.
3. Build `OrderRequest`. `client_order_id = proposal_id.hex` (idempotency
   key at exchange level).
4. Call `adapter.submit_order(request)`.
5. Write `orders` row with exchange response. `policy_version = git
   rev-parse HEAD`.
6. Update proposal: `status='executed'`, `executed_order_id=order_id`.
7. Post Discord update: "📥 Submitted · order_id <abbrev>".

### 8.3 Pre-trade policy rails

Twelve Phase 0 rails + four new Phase 2 rails. All run in order. First
block short-circuits.

**Phase 0 rails (from Phase 0 spec §5 Stage 6):**

1. `trading_enabled` global kill-switch.
2. `max_order_usd`.
3. `max_open_positions_per_symbol` (v1: 1).
4. `max_open_positions_total` (v1: 3).
5. `max_daily_realized_loss_usd` (trip also engages kill-switch).
6. `max_orders_per_day`.
7. Per-symbol cooldown after loss.
8. Stop-loss mandatory.
9. Stop-loss sanity band (`min_stop_distance_atr`, `max_stop_distance_atr`).
10. `max_leverage` (v1: 2).
11. Correlated-exposure cap (`max_correlated_usd`).
12. Symbol allowlist (`universe`).

**New Phase 2 rails:**

13. `first_trade_of_day_size_cap` — first live order of any UTC day is
    capped at `size_usd <= max_order_usd × first_trade_size_multiplier`
    (default 0.5). Released once at least one position closes positive
    that day.
14. `execution_mode_gating` — `execution_mode == 'live'` requires
    `trading_enabled == true` **and** a non-empty live API key. Either
    missing → block.
15. `stopless_runtime_assert` — after building the `OrderRequest`, assert
    `request.stop_loss is not None` and raise if violated. Defense in
    depth; should be unreachable via the code path.
16. `account_state_sanity` — pre-call `adapter.get_account_state()`;
    block if `free_margin_usd < size_usd × 1.2` (20% margin buffer).

Every block writes a `risk_events` row. Every allow records the
`policy_version` on the `orders` row.

### 8.4 Adapters

**`PaperAdapter`:**

- `submit_order`: reads the most recent candle for `symbol` at 5m
  timeframe. Market → `avg_fill_price = close`. Limit → marks order
  `open`; a Celery Beat task (`paper_match_orders`, 15s cadence) scans
  open paper orders and fills any whose limit price has been crossed by
  the latest 5m candle.
- `get_account_state`: synthesized from cumulative fills; starts at a
  configurable paper_starting_equity (env var, default $10,000).
- `get_positions`: materialized from paper fills.
- Attached stop-loss: a companion paper order with `reduce_only=true`;
  fills when the 5m candle low ≤ stop price (long) / high ≥ stop price
  (short).

**`CCXTProAdapter`:**

- `submit_order`: single atomic Binance call places the entry + a
  `STOP_MARKET reduceOnly` stop tied to the same symbol/side. Optional
  TP attached similarly. If TP is a list (multi-TP), each TP is a
  separate `reduceOnly` partial-close order.
- Failure modes: Binance rejection → `orders.status='rejected'`,
  `rejection_reason` populated, proposal → `'failed'`, DM operator.
- Network/timeout: 30s timeout per call; on timeout, check Binance for
  the `client_order_id` before retrying to avoid double-submit.

### 8.5 Position watchdog (Celery Beat, 60s)

`reconcile_positions` task:

1. `adapter.get_positions()` → authoritative.
2. Compare against `positions` table + open `orders`. Drift beyond
   tolerance → red Discord embed + `risk_events(severity='warning')`.
3. Drift exceeding `reconciliation_block_tolerance` → flip
   `trading_enabled = false` in-process, `risk_events(
   severity='kill_switch')`, red Discord embed "🚨 kill-switch:
   reconciliation drift".
4. For every open position, verify an attached stop exists on the
   exchange. Missing → `risk_events(severity='block')`, DM operator,
   auto-submit a replacement stop at the original level.
5. Recompute equity drawdown. Breach of `max_account_drawdown_pct` →
   kill-switch trip + optional flatten (gated by `auto_flatten_on_kill`
   policy key, default `false`).

### 8.6 Kill-switch auto-trip conditions

Any of the following trip the kill-switch. On trip, the state is
persisted in two places so a worker restart cannot silently re-arm: (a)
a `kill_switch_state` row is written/updated in Postgres with the trip
reason and timestamp, and (b) the execution-worker reads this row at
startup and at the beginning of every `submit_order` call, treating it
as an override on top of `policy.trading_enabled`. The worker refuses
to submit any order while the row is active. Red Discord embed on each
trip:

- `max_daily_realized_loss_usd` breached.
- `max_account_drawdown_pct` breached.
- Reconciliation drift > `reconciliation_block_tolerance`.
- Any order submission reached the `stopless_runtime_assert`.

**Resume is manual:** `myapp trading resume --ack-reason "<reason>"`. The
CLI writes a `risk_events(kind='manual_resume', severity='info',
context={reason})` row and flips the in-process flag back. No auto-resume
under any condition.

---

## 9. Runtime policy files

### 9.1 `runtime/CLAUDE.md`

Rewritten in Phase 2 Stage 1 from the current stub to a full persona
prompt. Target length 600–900 lines. Structure:

**Section 1 — Identity (~100 lines).** You are a seasoned discretionary
trader with deep experience in crypto perpetuals. Regime-adaptive: you
don't force trades, and you switch between trend-following and mean-
reversion playbooks based on what the market is doing. Capital
preservation is the first rule; the second rule is to let winners run
when the regime supports it. You trade the plan, not the hope. You size
small when uncertain and never risk more than the stop-loss distance
allows. You are always aware of funding costs on swings and of liquidity
at session opens/closes.

**Section 2 — Shared principles (~150 lines).** Expectancy framing.
Invalidation-first thinking (what would prove this thesis wrong? stop
there). Never without a stop. R-multiple math: plan the loss, let the
profit case describe itself. The asymmetry rule: win rate below 50% is
fine if avg-win × win-rate − avg-loss × loss-rate > 0. The "no trade" is
a valid outcome — the edge compounds when you pass on borderline setups.

**Section 3 — Per-regime playbooks (~250 lines).** One subsection per
`(trend_regime, vol_regime)` combination:

- `trend_up × normal` — trust `trend_pullback` long; pass on `divergence_*`
  short; consider `liquidity_sweep_daily` long after NY session sweep.
- `trend_up × expansion` — reduce size; trend can break.
  `squeeze_breakout` long is live; `funding_extreme` short requires
  exceptional evidence.
- `trend_up × squeeze` — wait; this is pre-breakout territory, not trade
  territory.
- `trend_down × *` — mirror of trend_up.
- `range × normal` — `range_rejection` both sides; `divergence_*` at
  range extremes; pass on trend archetypes entirely.
- `range × squeeze` — wait for expansion.
- `range × expansion` — range is breaking; pass until a new regime
  prints.
- `transition` — pass. Re-evaluate on next candle.

Each subsection names which archetypes to trust, which to distrust, what
the stop should key off (structural level vs. ATR multiple), and what
`find_similar_signals` sample size to demand before escalating.

**Section 4 — Per-archetype notes (~150 lines).** For each of the 8
archetypes: what the signal actually represents, what makes it genuine
vs. a fakeout, where the stop belongs, what the realistic target is, and
what `get_archetype_stats` should show for you to trust it.

**Section 5 — Hard rules (~50 lines).**

- Always call `find_similar_signals` before `save_decision`.
- `paper_trade` requires `similar_signals_count >= 10` OR exceptional
  evidence articulated in `similar_trades_evidence`; below that, downgrade
  to `alert` or `research_more`.
- Every `paper_trade` must come with a `propose_trade` call in the same
  session. A `save_decision(paper_trade)` without a proposal is incomplete.
- Never propose a trade without a stop-loss.
- Never propose a trade where `worst_case_loss_usd > max_order_usd ×
  stop_distance_fraction` — math must hold.
- Never attempt `decision == "live_order"` — the tool rejects it.
- On re-triage of the same signal, explicitly acknowledge the prior
  decision in the new rationale.

**Section 6 — Tool conventions (~50 lines).** Mandatory sequence:
`get_signal → get_market_snapshot → find_similar_signals →
get_archetype_stats → save_decision → [send_alert] → [propose_trade]`.
When to deviate, and how.

**Section 7 — Voice (~50 lines).** Rationale style examples. Short,
specific, numeric, acknowledges uncertainty. Three good-rationale
examples and three bad-rationale examples (what to avoid).

**Section 8 — Goals reference (~20 lines).** "Read `runtime/GOALS.md` on
every invocation. Every `alignment` field in a proposal must cite
specific goals this trade does or does not support."

### 9.2 `runtime/GOALS.md`

New file, narrative form, ~100–200 lines. Operator-authored content;
Phase 2 ships a template the operator fills in. Suggested sections:

- **Target return and horizon** — "Compound $X to $Y over N months."
- **Max acceptable drawdown** — "10% peak-to-trough; kill the system at
  15%."
- **Preferred hold durations** — e.g., "4h to 3d swings; avoid scalps
  shorter than 1h."
- **Avoided conditions** — e.g., "no trading during FOMC weeks; reduced
  size on weekends."
- **What success looks like** — how the operator will know the system is
  working in 3 months, 6 months, 12 months.
- **Non-goals** — what this system will not try to do.

CLAUDE.md Section 8 directs Claude to read this file on every invocation
and cite specifics in the `alignment` field of proposals.

---

## 10. `policy.yaml` additions

```yaml
# Phase 2 execution posture
trading_enabled: false              # global kill-switch — default disarmed
execution_mode: paper               # paper | live (testnet deferred)

# Proposal lifecycle
proposal_ttl_minutes: 15

# Live-mode safeguards
first_trade_size_multiplier: 0.5    # first live trade of UTC day capped

# Reconciliation tolerances
reconciliation_block_tolerance:
  position_base_drift_pct: 0.5      # > 0.5% base-size drift → kill-switch
  open_order_count_drift: 0         # any count drift → kill-switch

# Paper adapter
paper_starting_equity_usd: 10000

# Auto-flatten on kill-switch (default safe: off)
auto_flatten_on_kill: false

# Claude triage cap — now enforced
claude_daily_triage_cap: 20
```

Existing Phase 0/1 keys are unchanged. `execution_mode` values `paper`
and `live` are both supported; `testnet` is deferred.

---

## 11. CLI additions

```
myapp proposals [--status pending|approved|…]
myapp approvals [--recent N]          # alias: list recent approve/reject events
myapp orders [--status X]
myapp positions
myapp trading pause                    # sets trading_enabled=false in-process + persists
myapp trading resume --ack-reason "…"
myapp trading status
myapp flatten [symbol]                 # emergency close all (live adapter only)
myapp calibration [--lookback-days N]  # alert vs ignore median return at 24h horizon
myapp auth login                       # Claude Code OAuth in triage-worker container
myapp auth status
myapp doctor                           # extended: includes MCP, triage-worker, discord-listener
```

All commands are DB-direct (no Claude invocation). `calibration` is the
tool backing exit criterion #6.

---

## 12. Observability additions

**Prometheus metrics:**

- `ts_claude_invocation_seconds` — histogram of triage duration.
- `ts_claude_decisions_total{decision}` — counter.
- `ts_claude_tool_calls_total{tool}` — counter.
- `ts_claude_daily_cap_remaining` — gauge.
- `ts_proposals_total{status}` — counter.
- `ts_proposals_latency_seconds` — histogram proposed→approved.
- `ts_orders_submitted_total{mode,status}` — counter.
- `ts_risk_events_total{kind,severity}` — counter.
- `ts_reconciliation_drift_observations` — gauge.

**Grafana panels added:**

- Claude invocations / day (vs cap).
- Decision mix (`alert` / `paper_trade` / `ignore` / `research_more`) over time.
- Proposal funnel: proposed → approved → executed → realized.
- Alert vs ignore realized return at 24h horizon (the calibration panel).
- Kill-switch events (annotations on all panels).
- Reconciliation drift timeline.

---

## 13. Testing strategy

**Unit:**
- Each MCP tool with crafted inputs.
- `propose_trade` cross-check validators: math mismatch, sample-count
  mismatch, RR below minimum, missing stop.
- Every policy rail with crafted `OrderRequest` + `AccountState`.
- `PaperAdapter` fill logic (market, limit, stop).
- Discord embed rendering from proposal row.

**Integration (testcontainers):**
- Triage-worker end-to-end: seed a signal → trigger `triage_signal` →
  assert `claude_decisions` row written. Claude binary stubbed with a
  `fake-claude` script that reads the prompt and emits a canned JSON
  response.
- Approval loop: post a proposal → simulate Discord interaction payload
  → assert `trade_proposals` status transition and execution-worker
  enqueue.
- Execution end-to-end (paper): approved proposal → `submit_order` →
  `orders` row with simulated fill.
- Policy rail blocks: craft an `OrderRequest` violating each rail,
  assert `risk_events` row and proposal→`failed`.
- Kill-switch trip: simulate daily loss breach → assert
  `trading_enabled=false` + red Discord post + manual-resume-only.

**Replay:**
- Feed a recorded Phase 1 signal stream through the triage loop with
  `fake-claude` emitting deterministic decisions; assert reproducibility
  of `claude_decisions` + `trade_proposals` + `orders` tables.

**No live-adapter integration tests.** Live adapter is exercised only by
the operator flipping `execution_mode: live` in a controlled session.
Paper adapter carries the full integration surface.

---

## 14. Success criteria (exit criteria for Phase 2)

1. All 13 containers (`postgres`, `pgbouncer`, `redis`, `ingestor`,
   `feature-worker ×4`, `signal-worker`, `outcome-worker`,
   `celery-beat`, `prometheus`, `grafana`, `mcp-server`, `triage-worker`,
   `discord-listener`, `execution-worker`) stay green for 14+ days of
   continuous runtime.
2. Reconciliation gap = 0: every `claude_triaged` signal in the last
   14 days has a matching `claude_decisions` row.
3. ≥100 `claude_decisions` rows accumulated with `invocation_mode='triage'`.
4. Daily cap enforcement verified: on any UTC day exceeding the cap, the
   21st and later signals have `gating_outcome='daily_cap_hit'` and no
   `claude_decisions` row.
5. Every `claude_decisions` row has non-null `prompt_version`,
   `tools_called`, `decision`, `rationale`, `duration_ms`.
6. **Calibration (soft gate):** at the 24h horizon, median `return_pct`
   for `decision='alert'` signals ≥ median for `decision='ignore'`
   signals. Failure does not block Phase 2 completion but blocks arming
   live mode — it triggers a CLAUDE.md revision cycle.
7. `claude_decisions.error` populated in < 5% of rows.
8. Full Stage 2 MCP tool surface built and unit-tested.
9. Every approved proposal that reached `orders` has matching exchange
   state (reconciliation drift = 0 on paper; if live was armed, weekly
   drift check passes).
10. Zero orders submitted without attached stop (any violation is a P0
    bug; `stopless_runtime_assert` should make this unreachable).
11. Zero policy-rail bypasses: every `orders` row has a non-null
    `policy_version`; every block wrote a `risk_events` row.
12. `pytest` green in CI. Phase 0 + Phase 1 test suites stay green;
    Phase 2 tests added.

---

## 15. Non-goals (anti-scope)

- **No weekly retrospection loop.** The retrospection tools exist in
  Stage 2 but the `retrospect_week` Celery Beat job is Phase 3+.
- **No ML.** The rule-based regime classifier from Phase 1 continues to
  label every `features` row. ML lands Phase N.
- **No pgvector.** `find_similar_signals` is pure structural match.
- **No testnet adapter.** Paper + live only. Testnet is a Phase 2.5
  spec if the operator wants a live-hardening step before arming live.
- **No multi-operator.** Single `DISCORD_OPERATOR_ID` validated on
  approval. Adding a second approver is a future spec.
- **No modification tooling beyond submit/cancel.** Stop-loss moves /
  TP moves / size changes after submission are Phase 3.
- **No auto-flattening by default.** `auto_flatten_on_kill: false`.
  Operator can flip it per personal preference.
- **No profit-taking logic beyond TP attached at submit.** Partial
  profit-taking, trailing stops, scale-outs are Phase 3+.

---

## 16. Migration list

- `0010_phase2_execution_and_proposals.py` — creates all tables, then
  adds FKs. `trade_proposals.executed_order_id → orders.order_id` and
  `orders.proposal_id → trade_proposals.proposal_id` form a circular
  reference; resolution is to create both tables without those two FKs,
  then `ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY` each after both
  tables exist. Creation order: `orders`, `trade_proposals`,
  `order_modifications`, `positions`, `risk_events`, `alerts`, then the
  two circular-FK ALTERs. Also creates `kill_switch_state` singleton
  (with initial `id=1, active=false` row). Adds three indexes on
  `claude_decisions` and the `UNIQUE (signal_id, invocation_mode)`
  constraint. Adds CHECK constraints on the five proposal prose fields.

That is the only Phase 2 migration.

---

## 17. Implementation approach (sketch, not the plan)

Plan (to be written next via `superpowers:writing-plans`) will be
~50–70 TDD tasks grouped roughly:

### Stage 1 (the proof loop)

1. Schema migration 0010 + ORM models + Pydantic contracts.
2. MCP server skeleton: FastMCP, HTTP/SSE transport, Docker service.
3. `get_signal`, `get_market_snapshot` tools + unit tests.
4. `find_similar_signals` tool (structural match) + unit tests.
5. `get_archetype_stats` tool + unit tests.
6. `save_decision` tool + validation + unit tests.
7. `send_alert` tool + Discord webhook + idempotency unit test.
8. `propose_trade` tool + cross-check validators + unit tests.
9. `triage-worker` container: Node + Claude Code + OAuth volume.
10. `triage_signal` Celery task: canonical invocation function,
    `claude -p` subprocess, JSON parse, reconciliation.
11. Daily cap Redis gate in signal-worker.
12. `fake-claude` test harness for integration tests.
13. Triage end-to-end integration test.
14. `discord-listener` service: bot connection, interaction handler,
    operator validation.
15. Approval loop integration test.
16. `execution-worker` skeleton + adapter ABC.
17. `PaperAdapter` implementation + unit tests.
18. `submit_order` task + pre-trade policy rails (16 rails total).
19. Policy rail unit tests (one per rail).
20. `CCXTProAdapter` implementation + integration test (on testnet
    credentials if available, otherwise structural tests only).
21. Kill-switch auto-trip logic + tests.
22. Position watchdog (Celery Beat) + reconciliation test.
23. Stale proposal sweeper (Celery Beat).
24. `runtime/CLAUDE.md` authoring (8 sections).
25. `runtime/GOALS.md` template.
26. CLI additions: `proposals`, `approvals`, `orders`, `positions`,
    `trading`, `calibration`, `flatten`, `auth`, `doctor` extensions.
27. Grafana dashboard additions.
28. Prometheus metrics wiring.
29. End-to-end replay test.
30. Deploy runbook update (README).

### Stage 2 (tool expansion)

31. `get_feature_history`, `get_correlation_matrix`, `get_regime_context`.
32. `get_levels`, `get_recent_candles`.
33. `get_recent_outcomes`, `get_calibration`.
34. `propose_policy_change` + `proposed_changes/` directory handling.
35. CLAUDE.md update to reference the new tools in playbooks.

Checkpoints: after migration (1), after MCP Stage 1 tools (8), after
triage end-to-end (13), after approval loop (15), after paper execution
(19), after live adapter + kill-switch (22), after CLAUDE.md +
GOALS.md (25), after Stage 2 tools (34).

---
