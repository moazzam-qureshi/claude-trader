# HANDOFF — Wave 1: the remaining path to production, then go live

Resume the Phase 3 strategy pivot of trading-mcp-sandwich. ALL 25 Wave 1
strategy tasks (2.1–2.25) plus supporting tasks 2.26–2.29 are DONE and
pushed. This session: **build the two unbuilt links between "strategy
emits an OrderIntent" and "an order hits Binance", then go live with
the first $30 grid (Task 2.30) — but only after the operator confirms.**

Working directory: D:/Personal/Projects/trading-mcp-sandwich

---

## Pre-flight (run before any coding)

1. `git status` — clean tree on `phase-3-strategy-pivot`. Untracked
   `.claude/` + `scripts/probe_*.sh` + `scripts/inspect_claude_config.py`
   + `scripts/add_binance_mcp.sh` are pre-existing — leave alone.
2. `git log -1 --oneline` — should be `c22f21c chore: freeze
   discretionary trader path (preserve for analytics) (Wave 1 Task
   2.29)`. If different, STOP and ask.
3. `docker compose ps` — 11 services. Bring up if down (ask first).
4. `docker compose run --rm test \
     tests/integration/test_settings_repo_set.py \
     tests/integration/test_foundation_smoke.py \
     tests/integration/test_strategies_worker.py -q`
   — MUST be ~12+ passing. `settings_repo_set` (9) is the safety net,
   `foundation_smoke` is the Wave 0 contract, `strategies_worker` is
   what you'll be modifying.
5. `docker compose run --rm tools alembic current` — head `0017`.

Pre-existing test failures (do NOT re-flag, do NOT try to "fix" as part
of this work): `test_invocation.py` (3), `test_policy_phase2.py` (2),
`test_phase2_paper_e2e.py` (1) — the last one is a stale Celery
signal→order pipeline test, git-stash-verified to predate Task 2.29.

Read before coding:
- `docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md`
  (the design — esp. §3 architecture, §4 execution rail)
- `docs/superpowers/plans/2026-05-09-phase-3-strategy-pivot.md` (Task
  2.30 / 2.31 details)
- `src/trading_sandwich/strategies/worker.py` (the tick loop you're
  extending — its docstring already says "Submitting to ccxt is wired
  in Wave 1 when real strategies need it" — that's the task)
- `src/trading_sandwich/strategies/base.py` (`OrderIntent`,
  `StrategyContext`)
- `src/trading_sandwich/execution/worker.py` + `execution/adapters/*`
  (the execution rail — `submit_order(OrderRequest) -> OrderReceipt`)
- `src/trading_sandwich/contracts/phase2.py` (`OrderRequest`,
  `OrderReceipt`, `StopLossSpec`)
- `migrations/versions/0013_strategies.py` (the `strategy_orders` table
  — `(strategy_id, order_id, role, grid_level)` — already exists,
  unused so far)
- `src/trading_sandwich/execution/paper_match.py` (the existing
  paper-fill pattern — a good model for the fill-back loop)
- Memory `project_strategy_pivot.md` has the full state.

---

## What's done (don't redo)

- All 25 Wave 1 strategies, registered in `worker._default_registry()`,
  487/487 in the regression sweep.
- Strategy-worker ticks every 30s, crash-safe, persists `strategy_state`.
- Backtest framework (`src/trading_sandwich/backtest/`) — Task 2.26.
- Portfolio-strategist persona: `runtime/CLAUDE.md`, SOUL.md, GOALS.md
  rewritten (Tasks 2.27–2.28); old personas at `.heartbeat-trader.bak`.
- `propose_trade` frozen behind `emergency_override=True` (Task 2.29).
- The execution rail itself EXISTS from Phase 2: `execution/adapters/`
  (paper, ccxt_spot, ccxt_live), `execution/worker.py` with `_adapter()`
  mode-select, `policy_rails.py`, `kill_switch.py`, `watchdog.py`,
  `paper_match.py`. It was built for the Phase 2.7 *single-trade*
  flow (proposal → approve → submit) — you're adding a *strategy-intent*
  path alongside it, not replacing it.

---

## The two blockers — build these, in this order

These are NOT numbered plan tasks; they're prerequisites for Task 2.30
to actually function. Treat each as its own TDD task with its own
commit (or two), following the project discipline (Docker-only, RED →
GREEN → commit, Conventional Commits, never skip hooks).

### Blocker A — snapshot plumbing into the worker

**Problem:** `worker._tick_one_strategy` calls
`strategy.tick(ctx, snapshot={})` — an *empty* snapshot. Every Wave-1
integration test monkeypatches `_tick_one_strategy` to inject the
fields the strategy needs. In production a deployed strategy ticks
every 30s and gets nothing — it can't compute a single decision.

**Build:** a `build_snapshot(symbol)` (likely
`src/trading_sandwich/strategies/snapshot.py`) that reads the latest
`raw_candles` + `features` row for the symbol and returns the snapshot
dict the strategies expect. The full field set across all 25 strategies
(see memory's per-strategy table or grep `requires snapshot[`):

| Field | Source |
|---|---|
| `mid_price` | latest `raw_candles.close` (or `(close+open)/2`) |
| `now` | `datetime.now(timezone.utc)` |
| `rsi` | `features.rsi_14` |
| `bb_lower`, `bb_upper` | `features.bb_lower`, `features.bb_upper` |
| `price_z_score` | NOT a feature yet — needs `price_zscore_20` added to `features/compute.py` (only `volume_zscore_20` exists). Optional for now: build the snapshot without it; the only strategy that needs it (A7) just won't fire until it's added. |
| `atr_percentile` | `features.atr_percentile_100` |
| `atr` | `features.atr_14` |
| `atr_pct` | `features.atr_14 / raw_candles.close` (compute it) |
| `ma_fast`, `ma_slow`, `ma_n` | `features` EMAs — ema_55 exists; pick which feeds which (e.g. ema_21→fast if it exists, ema_55→slow, ema_55→ma_n). Memory notes ema_55 is the ~MA50 proxy. |
| `reference_price` | prior bar's `raw_candles.close` |
| `donchian_high`, `donchian_low` | NOT features yet — needs `compute_donchian` or rolling max/min over a window. Optional for now (only D2 needs them). |
| `bullish_1d`, `bullish_4h`, `bullish_1h` | NOT features yet — needs per-timeframe trend bias (e.g. close > EMA on that timeframe). Optional for now (only D5 needs them). |
| `btc_dominance_rising` | TradingView BTC.D feed — out of scope for this session; only E3 needs it. |

**Pragmatic scope:** wire the fields that come straight from
`raw_candles` + `features` (mid_price, now, rsi, bb_*, atr_percentile,
atr, atr_pct, ma_*, reference_price). That covers A1–A6, A8, B1–B4, B7,
C1–C4 (the grid/mean-rev/DCA/rebalance bulk — including the A1 grid
that Task 2.30 deploys, which only needs `mid_price` + `now`). The
exotic fields (price_z_score, donchian, multi-TF, BTC.D) can land in a
follow-up — the strategies that need them simply don't fire until then,
which is fine: those aren't the first-deployment candidates.

Then change `worker._tick_one_strategy` to call
`build_snapshot(row.symbol)` instead of `snapshot={}`. The Wave-1
integration tests that monkeypatch `_tick_one_strategy` still work
(they replace the whole function); add a new test that the real worker
tick passes a populated snapshot to a strategy.

**Watch out:** strategies raise `KeyError` on a missing required
snapshot key. If `build_snapshot` can't produce a field (no features
row yet, warm-up period), either omit the strategy from the tick (skip
+ log, like the unknown-strategy_type path) or give it a sane fallback
— but don't let a missing field crash the whole worker. The
`backtest/replay.py::default_price_snapshot_builder` has a good pattern
for warm-up fallbacks (close for EMAs, ~1% ATR, RSI 50, bands ±2%).

### Blocker B — the strategy ↔ execution-rail bridge (both directions)

**Problem half 1 (outbound):** the worker collects `OrderIntent`s from
`tick()` and currently just *logs* them. Nothing converts them to
orders.

**Problem half 2 (inbound / fill delivery):** when an order fills,
nothing writes back into `strategy_state`. Grid strategies look at
`state['levels'][i]['filled_buy']` to decide whether to place a sell at
rung i+1 — but nothing ever flips it to True, so grids never place
their sell legs. The DCA/rebalance families estimate position units as
`size_usd / mid` because they never see the real fill quantity.

**Build, outbound:**
1. A converter `OrderIntent → OrderRequest` (note: `OrderRequest`
   requires a `stop_loss: StopLossSpec` — strategies don't carry one,
   so synthesise a wide/no-op stop, or extend the rail to accept
   `stop_loss=None`; the latter is cleaner — check what `policy_rails`
   does with it). `OrderRequest.side` is `Side` (long/short) — strategy
   `OrderIntent.side` is always `'long'`, so that maps directly.
2. **The `OrderIntent.direction` gap** — `OrderIntent` has no field for
   "this is a sell": `role='entry'/'rebalance'` are buys, `role='exit'/
   'take_profit'/'stop_loss'` are sells, but `role='rebalance'` is used
   for *both* up- and down-sizes by the rebalance family
   (`rebalance/_base.py`) and that's ambiguous. **Recommended fix:** add
   `direction: Literal['buy','sell'] = 'buy'` to `OrderIntent` in
   `base.py`, set `direction='sell'` on every sell-emitting branch
   (`rebalance/_base.py`, `grid/_base.py emit_sells_for_fills`,
   `grid/reverse.py`, `mean_reversion/_base.py`,
   `mean_reversion/range_expansion.py`, `hybrid/hodl_plus_plus.py`'s
   grid-sell + the rebalance-toward-value call,
   `trend/_base.py apply_binary_trend_signal`'s exit branch). This is a
   focused cross-cutting change — its own commit. Update the backtest
   fill_sim to use `direction` instead of role-inference (closing the
   "rebalance-as-buy" limitation noted in `backtest/__init__.py`). The
   live execution rail needs this distinction anyway, so it's not
   backtest-specific.
3. In `_tick_one_strategy` (or a new `_dispatch_intents` step): for each
   intent, convert → submit via the execution adapter (`execution/
   worker.py::_adapter()` gives `(adapter, mode)`; in paper mode it's
   the PaperAdapter, in live mode CCXTSpotAdapter — gated by
   `EXECUTION_MODE` env + key presence, see
   `test_execution_mode_live_blocks_without_keys.py`). Persist an
   `orders` row + a `strategy_orders` row `(strategy_id, order_id, role,
   grid_level)` linking them. Run intents through `policy_rails` first
   (the same gate the proposal path uses) — a strategy that somehow
   emits something the rails block should be rejected, not submitted.

**Build, inbound (fill delivery):** a Celery beat task (model it on
`paper_match.py`) — or extend `paper_match` — that, after an order
fills, finds the `strategy_orders` row for that order, loads the
strategy's `strategy_state`, and updates it: for a grid buy fill, set
`state['levels'][i]['filled_buy'] = True` (i from `strategy_orders.grid_level`);
for a grid sell fill, mark the rung sold; for DCA/rebalance, correct
`position_units` with `OrderReceipt.filled_base`. Use the optimistic
lock on `strategy_state.updated_at` (`repo.save_state` does this
already). Live fills come from the adapter's order-status poll (see how
`execution/watchdog.py` reconciles); paper fills come from
`paper_match`.

**Scope warning:** this is the riskier blocker. The execution rail was
built for the Phase 2.7 single-trade flow; reconciling the
strategy-intent model with `OrderRequest`/`OrderReceipt`/`policy_rails`
may surface more than it looks like from the outside. Budget 1–2
sessions for B alone. If `OrderRequest`'s mandatory `stop_loss`, or the
`orders` table schema, or `policy_rails` resists the strategy path,
**stop and flag it** rather than forcing a hack — a clean extension of
the rail is worth a session of design.

### After A + B: an end-to-end paper test

Before going anywhere near live: an integration test that deploys A1
Standard Grid in **paper mode**, runs several worker ticks, drives
synthetic candles through `raw_candles`, and asserts: the grid's buy
limits land as `orders` rows + `strategy_orders` rows, `paper_match`
fills the ones whose limit is crossed, the fill-back loop flips
`filled_buy=True` on the right rungs, and the next tick emits the
sell-against-fill legs. That test passing is the green light to ask the
operator about Task 2.30.

---

## Task 2.30 — first live deployment (HARD GATE)

Once A + B + the end-to-end paper test are done and green:

**STOP. Ask the operator: "Blockers A and B are built, the paper E2E
passes — go-ahead to deploy grid-btc-1 live with $30?"** Do NOT deploy
without an explicit "yes". This is real money on real Binance — the
plan, every prior handoff, and the rewritten GOALS.md all say this
deployment needs operator confirmation regardless of test status.

On a "yes": deploy via `deploy_strategy(strategy_type="grid_standard",
symbol="BTCUSDT", capital_usd=30, params={low, high, levels:5,
...})` — `low`/`high` from the current `get_regime_signals("BTCUSDT")`
classification (a sensible range around current price for the regime).
Confirm `EXECUTION_MODE` is `live` and keys are present first. Monitor.
Commit: `chore: first Wave 1 live deployment — grid-btc-1`.

Then Task 2.31 (progressive rollout) is a 7-day-monitoring-then-allocate
exercise, not a coding task — the strategist's allocation decisions
playing out.

---

## Discipline (non-negotiable, same as always)

- Docker-only: `docker compose run --rm test/tools <args>`. Never
  install anything on the host.
- ONE TASK = ONE COMMIT. TDD: RED → GREEN → commit. (Blocker B may
  legitimately be 2–3 commits: the `OrderIntent.direction` change, the
  outbound bridge, the inbound fill-back loop — each its own commit.)
- Conventional Commits. Never skip hooks. Never destructive git.
- Never weaken `tests/integration/test_settings_repo_set.py` (the 9
  safety tests pinning Claude can't raise its own circuit breakers).
- Halal-spot inviolable: `OrderIntent.side` stays `Literal['long']`.
  The new `direction` field is `'buy'|'sell'` — that's *trade
  direction*, not *position side*; a 'sell' only ever reduces an
  existing long, never opens a short. Tier 1 keys (`longs_only`,
  `max_leverage`, excluded universe, kill switches, drawdown breakers)
  stay file-only and untouchable. NEVER loosen any of this.
- Discord `/settings` (Tier 3) vs `/safety` (Tier 2 operator-only)
  split is STRUCTURAL — don't collapse.
- Cold-start regime classification does NOT pivot (first 2-read is
  baseline). Don't "fix" without a spec amendment.
- Push at the end of each blocker (operator preference: batch at
  natural boundaries; remote is HTTPS — `git push` just works).

## Ask the operator before coding (one batch)

1. Bring docker compose up if down? (yes/wait)
2. Proceed straight into Blocker A (snapshot plumbing)? Or do you want
   to review/scope something first?
3. For the eventual `OrderIntent.direction` change in Blocker B — OK to
   add a field to the `OrderIntent` Pydantic model in `base.py`
   (default `'buy'`, so the 25 strategies still work; sell-emitting
   branches set `'sell'`)? It's the clean fix for the rebalance-side
   ambiguity and the live rail needs it.

Then proceed: A → B → end-to-end paper test → **STOP for Task 2.30
go-ahead** → live deploy on "yes" → 2.31.
