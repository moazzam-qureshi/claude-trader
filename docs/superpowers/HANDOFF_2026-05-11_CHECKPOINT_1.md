# Handoff — CHECKPOINT 1: Wave 0 complete (2026-05-11)

Phase 3 plan tasks 1.1 → 1.16 are all shipped. Branch
`phase-3-strategy-pivot` is at 35 commits ahead of origin/main, all
local (operator preference). Foundation smoke test
(`test_foundation_smoke.py::test_wave_0_foundation_end_to_end`) passes
end-to-end. Final regression sweep: **340/340** across the full unit
suite + every Phase 3 integration suite.

This is CHECKPOINT 1 from the plan. Operator review the foundation
before Wave 1 begins.

---

## What ships in Wave 0

### Universe (Task 1.5)
- `policy.yaml` expanded to spec §6.1
- `watchlist` tier renamed to `active` (full diff across 12 files)
- `excluded` sub-categorized into `symbols_lending` /
  `symbols_perp_protocols` / `symbols_memecoins`
- 32 active candidate symbols (3 core + 22 active + 7 observation)
- 15 structurally-excluded symbols locked
- `runtime/CLAUDE.md` and `runtime/GOALS.md` still mention `watchlist`
  by deliberate operator decision; they get full rewrites at Wave 1
  tasks 2.27/2.28.

### Strategy contract (Task 1.6)
- `src/trading_sandwich/strategies/base.py`
- `Strategy` ABC: `tick(ctx, snapshot)`, `graceful_shutdown(ctx)`,
  `emergency_stop(ctx)`, `expected_return_for_regime(regime)`
- `StrategyStatus` enum: pending → active → paused → winding_down →
  completed (+ errored from any non-terminal). Matches migration 0013
  CHECK constraint exactly.
- `OrderIntent` halal-gated: `side: Literal['long']` only — short
  intents fail at construction time, before reaching the adapter rail.
- `StrategyContext`: per-tick payload the worker hands the strategy
  (id, type, symbol, params, mutable state, capital).
- `next_status(current, action)` enforces the state machine in Python.

### Strategy persistence (Task 1.7)
- `src/trading_sandwich/strategies/repo.py`
- CRUD + every state transition (`mark_active`/`mark_paused`/
  `mark_resumed`/`mark_winding_down`/`mark_completed`/`mark_errored`)
- `save_state` with optimistic locking on `strategy_state.updated_at`
  → `StaleStateError` on race.
- `list_active()` returns active+paused (the running fleet).
- Same per-call NullPool engine pattern as `settings/repo.py`.

### Regime classifier (Task 1.8)
- `src/trading_sandwich/regime/strategy_classifier.py`
- Two layers: `classify_signals` (pure, unit-tested) +
  `classify_and_log` (DB-backed, integration-tested).
- ADX + ATR% + MA structure → 5 regimes (TREND_UP/DOWN, RANGE_VOLATILE/
  QUIET, TRANSITIONING). Spec §3.3 verbatim.
- Uses `ema_55` as proxy for spec's `ma50` (no EMA-50 in feature stack
  — documented).
- 2-consecutive hysteresis. **Cold-start design choice:** the first
  cleared run establishes baseline; no pivot row written. Pivot only
  fires for transitions between two known states. Pin this; don't
  "fix" without an explicit spec amendment.
- Existing Phase 1 `regime/classifier.py` (per-candle trend × vol for
  feature detection) is untouched and continues to feed
  `features/compute.py`.

### Strategy↔regime compatibility (Task 1.9)
- `src/trading_sandwich/strategies/regime_compat.py`
- `STRATEGY_CATALOG` frozenset of 37 spec §6.2 strategy IDs.
- `policy.yaml` `strategy_regime_compatibility` block (full map).
- Wildcard `"*"` for always-on strategies; specific lists for
  regime-coupled ones.
- Loader rejects unknown IDs at parse time. `is_compatible()`
  fail-closed on unknown strategy.

### Performance tracker (Task 1.10)
- `src/trading_sandwich/strategies/performance.py`
- `compute_realized_pnl(strategy_id, since)` — joins
  `strategy_orders` → `orders` for entry cost vs exit proceeds.
- `evaluate(strategy, current_regime, window_days, threshold_pct)` —
  flags underperformance vs `expected_return_for_regime` × scale.
- Realized PnL only; unrealized deferred (needs current-price lookup).

### MCP read tools (Task 1.11)
- `src/trading_sandwich/mcp/tools/strategies_read.py`
- `list_strategies(active_only=True)`,
  `get_strategy_performance(strategy_id, since='7d')`,
  `get_account_allocation()`, `get_regime_signals(symbol)`.
- Decimals → strings (JSON has no Decimal); datetimes → ISO.
- Empty/missing state returns nulls in dicts, never raises.

### MCP active commands (Task 1.12)
- `src/trading_sandwich/mcp/tools/strategies_command.py`
- 7 tools: deploy/wind_down/pause/resume/adjust_allocation/
  adjust_params/override_regime.
- Every successful call → `portfolio_decisions` audit row with
  `decided_by='claude'`, `prompt_version=git HEAD`.
- Errors return as `{status:'error', error:..., message:...}` —
  never raise to MCP. Catalog miss, invalid transitions, regime
  override duration cap, etc all surface as structured errors.

### Discord slash commands (Task 1.13)
- `src/trading_sandwich/discord/strategies_handlers.py` +
  `_register_strategies_commands` in listener.
- `/strategies list/pause/resume`, `/regime override` (operator-only
  via DISCORD_OPERATOR_ID gate, writes triggered_by=
  `'operator_override'`), `/equity`, `/decisions last`.
- Same string-in/markdown-out pattern as AM-6 settings handlers.
- Skipped for now: `/strategies adjust` modal (lives in 1.14),
  `/backtest` (no engine yet — Wave 1 task 2.26), `/sweep`
  (no withdrawal flow on path).

### Discord buttons + adjust modal (Task 1.14)
- `src/trading_sandwich/discord/strategies_buttons.py`.
- 4-button view: Pause / Resume / Wind Down / Adjust… on strategy
  notification cards. custom_id format `strat_<action>:<sid>`.
- `AdjustParamsModal` for JSON-payload params edits; validated as
  JSON object on submit.
- Listener `on_interaction` tries strategy buttons first; falls
  through to legacy proposal approve/reject on miss.

### Strategy worker (Task 1.15)
- `src/trading_sandwich/strategies/worker.py`.
- `tick_all_strategies(registry)` — pure async core. Iterates
  list_active, filters to active-only, ticks each via registry,
  saves state, updates last_tick_at. Crash isolation: bad
  strategy → mark_errored + continue.
- `strategies_tick_celery` — Celery beat task firing every 30s.
- `docker-compose.yml` adds `strategy-worker` service consuming
  the `strategies` queue.
- Production registry empty for Wave 0; Wave 1 strategies populate
  it as they land.

### Foundation smoke test (Task 1.16)
- `tests/integration/test_foundation_smoke.py`
- Single end-to-end test: deploy → tick → query → pause →
  resume → wind_down. 4-row audit trail in `portfolio_decisions`.
- Passes ✓.

---

## What's still in flight (carries to Wave 1)

- `runtime/CLAUDE.md` and `runtime/GOALS.md` still reference the
  Phase 2.7 heartbeat-trader persona. Get full rewrites at plan
  tasks 2.27 + 2.28 (portfolio strategist persona).
- Strategy worker registers no production strategies — Wave 1
  populates `_default_registry()` as each strategy ships.
- `runtime/CLAUDE.md.heartbeat-trader.bak` backup not yet taken
  (also part of 2.27).
- The two `\xc2\xa7` literal bytes in commit messages c7033e6 +
  6597a2f are cosmetic and not worth fixing.
- 22 commits unpushed at start of session, +13 this session,
  total 35 unpushed. Operator's call when to push.
- 5 pre-existing baseline test failures in
  `tests/unit/test_invocation.py` (3) and
  `tests/unit/test_policy_phase2.py` (2). Stale assertions, NOT
  caused by this session's work — verified by stash test.

## Live state

- Branch `phase-3-strategy-pivot`: 35 commits ahead of origin/main.
- Postgres alembic head: 0017.
- Capital: ~$113 USDT (operator transfer-out 2026-05-11).
- `trading_enabled`: false.
- `longs_only`: true. `max_leverage`: 1.
- Universe: 32 active candidates per spec §6.1.
- Discretionary heartbeat trader still gated. No live trades.
- All 11 Docker services running.

## How to resume Wave 1 next session

1. `git status` — confirm clean tree on branch `phase-3-strategy-pivot`
   (only pre-existing untracked files: `.claude/`, `scripts/probe_*`,
   `scripts/inspect_claude_config.py`, `scripts/add_binance_mcp.sh`).
2. `git log --oneline -5` — head should be `b1bdbe8 test: foundation
   integration smoke test`.
3. `docker compose ps` — bring up if down.
4. `docker compose run --rm tools alembic current` — head is `0017`.
5. Run the safety regression to confirm the foundation is intact:

       docker compose run --rm test \
         tests/integration/test_settings_repo_set.py \
         tests/integration/test_foundation_smoke.py -q

   MUST be 9 + 1 = 10 passing.

6. Read in order before any code:
   - `docs/superpowers/HANDOFF_2026-05-11_CHECKPOINT_1.md` (this doc)
   - `docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md`
     §2.1 (Category A range/volatility strategies — A1 Standard Grid
     definition)
   - `docs/superpowers/plans/2026-05-09-phase-3-strategy-pivot.md` —
     Phase 2 / Wave 1 task list. First task: 2.1 A1 Standard Grid.
   - `src/trading_sandwich/strategies/base.py` (the contract you'll
     subclass)
   - `tests/integration/test_strategies_worker.py` (registry pattern)
   - `tests/integration/test_foundation_smoke.py` (end-to-end shape)

## Picking up at Wave 1 task 2.1 — A1 Standard Grid

Per plan task 2.1: implement `strategies/grid/standard.py`. The
strategy task pattern is:

  1. RED — unit tests for tick logic + integration test for end-to-end
     deploy/tick/order-flow
  2. GREEN — minimal impl
  3. DRY-RUN verify — deploy via MCP with commit_orders=False, observe
     one tick, verify intents
  4. Live capability — wire to execution-worker (default OFF per
     instance)
  5. Backtest — historical replay vs documented baselines
  6. Commit

The grid strategy:
- Defines a price ladder between `params['low']` and `params['high']`
  with `params['levels']` rungs.
- On each tick: if a buy rung's filled, place a sell at the next-
  higher rung; if a sell rung's filled, place a buy at the next-
  lower rung. State tracks which rungs hold open orders.
- `expected_return_for_regime(RANGE_VOLATILE)` returns ~3–5%/mo
  baseline.

Then strategies tasks 2.2 → 2.25 follow the same pattern. Supporting
tasks 2.26 (backtest framework), 2.27 (portfolio strategist runtime/
CLAUDE.md), 2.28 (SOUL+GOALS), 2.29 (freeze discretionary path), 2.30
(first live deployment), 2.31 (progressive rollout) interleave per
plan order.

CHECKPOINT 2 is at the end of Wave 1 stabilization (30 days post-
deployment of first Wave 1 strategy).

## Reminders that still apply

- Docker-only execution. ONE TASK = ONE COMMIT. TDD throughout.
- Conventional Commits. Never skip hooks. Never destructive git.
- `tests/integration/test_settings_repo_set.py` 9/9 is the safety net
  preventing Claude from raising its own circuit breakers. Don't
  weaken without a spec amendment.
- Discord `/settings` (Tier 3) vs `/safety` (Tier 2) split is
  STRUCTURAL — different commands, different authorities. Don't
  collapse for ergonomics.
- Cold-start regime classification does NOT pivot (baseline only).
  Pin this behavior; only "real" transitions write a pivot row.
- Halal-spot only. `OrderIntent.side` is `Literal['long']`. Tier 1
  keys (max_leverage, longs_only, excluded sets) are file-only and
  inviolable through every path.

---

*Foundation: shipped. Wave 1: starts at task 2.1 next session.*
