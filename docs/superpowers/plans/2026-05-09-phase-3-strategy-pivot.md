# Phase 3 — Strategy Pivot: Implementation Plan

> **Status:** Ready for execution.
> **Spec:** `docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md`
> **Total tasks:** ~57 across 4 phases (Foundation + 3 implementation waves), ~10–13 weeks calendar.
> **Discipline:** TDD throughout (RED test → GREEN implementation → commit per task). No batched commits. No tier-gated feature freezes — strategies ship in dependency order; operator can deploy any implemented strategy at any time.

---

## Handoff to Next Session

**You are picking up Phase 3 of the trading-mcp-sandwich pivot from discretionary heartbeat trading to mechanical strategy trading with Claude as portfolio strategist.**

### What's been decided (read the spec first)

1. **Read `docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md` in full before starting any task.** All design decisions are locked there. Do not re-litigate.
2. The Phase 2.7 heartbeat trader (discretionary) is being replaced because it failed in production: over-conservative pattern evaluation, loss-heavy realised PnL, sample-size convergence problem.
3. Claude's new role is **portfolio strategist** — decides which mechanical strategies to deploy on which symbols at what capital, NOT individual trade timing.
4. **All ~37 strategies are first-class platform citizens.** No V1/V2/V3 tier-gating. Implementation order is purely dependency-based: strategies needing existing feature stack ship in Wave 1; strategies needing new external feeds (CFGI, on-chain) ship in Waves 2 & 3 as feeds are integrated.
5. **Full halal candidate universe** active (~30 coins across core + active + observation tiers per spec §6.1). Excluded set explicitly enumerated for haram (lending, perps, yield) and operator-policy (memecoins).
6. Migration is parallel-running: discretionary trader stays alive until first Wave 1 strategies prove out.

### What to do first

1. Read the spec.
2. Confirm with operator: capital still ~$167 USDT? Universe per spec §6.1 acceptable? Discord channels decided? CFGI/on-chain feed approvals?
3. Execute tasks in order. Each task: RED test → GREEN implementation → commit → next.
4. Stop at every CHECKPOINT for operator review.

### Important conventions (from project CLAUDE.md)

- **Docker-only execution.** Never run pytest/alembic/python on host. Use `docker compose run --rm test/tools <args>`.
- **Default to no comments.** Code self-explanatory; comment only when WHY is non-obvious.
- **Conventional Commits.** `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `ci:`. Look at git log for style.
- **Never skip hooks.** No `--no-verify`, no `--no-gpg-sign`. Fix failures, don't bypass.
- **Never use destructive git** without operator confirmation.
- **Use superpowers skills:** `test-driven-development`, `systematic-debugging`, `executing-plans`. Rigid skills — follow exactly.
- **One task = one commit.** Don't batch. Don't amend.

### Halal rules (non-negotiable, enforced in code)

- `max_leverage: 1` always
- Longs only — `propose_trade(side='short', ...)` is rejected at adapter layer
- No funding-rate strategies, no perps, no margin, no lending protocols
- Excluded universe symbols are LOCKED — Claude cannot un-exclude them
- See `runtime/CLAUDE.md` halal warning block (preserve in new portfolio strategist version)

### Strategy task pattern

Every strategy implementation task follows the same TDD shape:

1. **RED** — unit tests for tick logic + integration test for end-to-end deploy/tick/order-flow
2. **GREEN** — minimal implementation that passes tests
3. **DRY-RUN verify** — deploy via MCP with `commit_orders=False`, observe one tick, verify intents match expected
4. **Live capability** — wire to execution-worker (default OFF; operator must explicitly enable per instance)
5. **Backtest** — run historical replay, compare to documented baselines (e.g., DCA +202%, F&G contrarian +1,145%, Shrimpy 15% +77%)
6. **Commit** — single conventional commit per strategy

---

## Phase 1 — Foundation (Wave 0, Tasks 1–15, ~2 weeks)

**Goal:** Strategy Engine base + Strategy Manager + new MCP tools + Discord controls. No live trading yet.

**Checkpoint at end of Phase 1:** Operator review. All strategies must be ready to implement on this foundation.

### Task 1.1 — Branch + spec review confirmation
- Create branch `phase-3-strategy-pivot` from `main`
- Read full spec
- Capture base commit SHA
- **Commit:** `chore: open phase-3 branch + base commit ref`

### Task 1.2 — Migration 0013 (strategies, strategy_state, strategy_orders)
- **RED:** test that fails because tables don't exist
- **GREEN:** Alembic migration matching spec §5.1
- Run `docker compose run --rm tools alembic upgrade head` to verify
- **Commit:** `feat(db): migration 0013 — strategies, strategy_state, strategy_orders tables`

### Task 1.3 — Migration 0014 (regime_classifications, regime_pivots)
- **RED:** test for regime tables
- **GREEN:** migration per spec §5.2
- **Commit:** `feat(db): migration 0014 — regime_classifications, regime_pivots`

### Task 1.4 — Migration 0015 (portfolio_decisions)
- **RED:** test for portfolio_decisions table
- **GREEN:** migration per spec §5.3
- **Commit:** `feat(db): migration 0015 — portfolio_decisions`

### Task 1.5 — Universe expansion (policy.yaml)
- **RED:** test that universe loader handles new tier structure (core + active + observation + excluded with sublist categorization)
- **GREEN:** Replace `policy.yaml` universe block per spec §6.1. ~30 active candidate coins. Pydantic schema validates structure. Existing universe-mutation logic respects new tier semantics.
- **Commit:** `feat(universe): full halal candidate universe per spec §6.1`

### Task 1.6 — Strategy ABC + state machine
- **RED:** abstract methods enforced, lifecycle transitions valid
- **GREEN:** `src/trading_sandwich/strategies/base.py` with `tick()`, `graceful_shutdown()`, `emergency_stop()`, `expected_return_for_regime()`. State machine: `pending → active → paused → winding_down → completed`.
- **Commit:** `feat(strategies): Strategy base class + state machine`

### Task 1.7 — Strategy state persistence
- **RED:** read/write strategy state to DB, idempotent upsert, optimistic locking
- **GREEN:** `strategies/repo.py`
- **Commit:** `feat(strategies): strategy state repo (read/write)`

### Task 1.8 — Regime classifier (deterministic)
- **RED:** given known indicator values, returns expected regime
- **GREEN:** `src/trading_sandwich/regime/classifier.py` per spec §3.3. Multi-signal: ADX + ATR% + MA structure. Logs to `regime_classifications`.
- Hysteresis: requires 2 consecutive same classifications before pivot fires
- **Commit:** `feat(regime): deterministic regime classifier with hysteresis`

### Task 1.9 — Strategy ↔ regime compatibility config
- **RED:** test config loads, validates strategy IDs against catalog, rejects unknown IDs
- **GREEN:** policy.yaml additions per spec §6.2 + Pydantic schema + loader
- **Commit:** `feat(regime): strategy-regime compatibility config`

### Task 1.10 — Performance tracker
- **RED:** computes per-strategy 30d PnL, compares to expected_return_for_regime, flags underperformers at <50%
- **GREEN:** `src/trading_sandwich/strategies/performance.py`
- **Commit:** `feat(strategies): performance tracker with regime-expected comparison`

### Task 1.11 — Strategy MCP read tools
- **RED:** `list_strategies`, `get_strategy_performance`, `get_account_allocation`, `get_regime_signals` return correct data
- **GREEN:** `src/trading_sandwich/mcp/tools/strategies_read.py`
- **Commit:** `feat(mcp): strategy read tools`

### Task 1.12 — Strategy MCP active tools
- **RED:** `deploy_strategy`, `wind_down_strategy`, `pause_strategy`, `resume_strategy`, `adjust_allocation`, `adjust_params`, `override_regime` create correct DB rows + emit events
- **GREEN:** `src/trading_sandwich/mcp/tools/strategies_command.py`
- **Commit:** `feat(mcp): strategy active commands`

### Task 1.13 — Discord slash command framework
- **RED:** new slash commands register on listener startup, button interaction handler dispatches correctly
- **GREEN:** Extend `src/trading_sandwich/discord/listener.py` with `/strategies`, `/regime`, `/backtest`, `/equity`, `/decisions`, `/sweep`. Use existing approval.py pattern.
- **Commit:** `feat(discord): slash commands for strategy control`

### Task 1.14 — Discord interactive components
- **RED:** button + modal handlers test
- **GREEN:** `discord/buttons.py` + `discord/modals.py`. Persistent button views for notification cards.
- **Commit:** `feat(discord): interactive button and modal components`

### Task 1.15 — strategy-worker Celery service
- **RED:** worker picks up active strategies, calls `tick()` every 30s, persists state, recovers from crash
- **GREEN:** `src/trading_sandwich/strategies/worker.py` + Celery task + docker-compose service entry
- **Commit:** `feat(strategies): strategy-worker Celery service`

### Task 1.16 — Foundation integration smoke test
- **RED:** end-to-end: deploy NoOpStrategy via MCP, worker ticks it, performance tracker reads it, Discord notification fires
- **GREEN:** all components wired
- **Commit:** `test: foundation integration smoke test`

### **CHECKPOINT 1** — Operator review

Foundation solid. Tests pass. Discord controls work. Regime classifier produces sensible output on live BTC/ETH data. Universe loaded correctly.

---

## Phase 2 — Wave 1: Self-contained Strategies (Tasks 16–45, ~3 weeks)

**Goal:** Implement all strategies that need only the existing feature stack (RSI, ATR, ADX, BB, EMA/SMA, price/volume — already in Phase 1 features). 25 strategies + supporting tasks. Portfolio strategist persona shift mid-wave.

Each strategy task follows the strategy task pattern (RED → GREEN → DRY-RUN → live capability → backtest → commit). Strategies ship in this order; each is independently deployable upon completion.

| Task | Strategy ID | File |
|---|---|---|
| 2.1 | A1 Standard Grid | `strategies/grid/standard.py` |
| 2.2 | A2 Infinity Grid | `strategies/grid/infinity.py` |
| 2.3 | A3 Geometric Grid | `strategies/grid/geometric.py` |
| 2.4 | A4 Reverse Grid | `strategies/grid/reverse.py` |
| 2.5 | A5 RSI Mean Reversion | `strategies/mean_reversion/rsi.py` |
| 2.6 | A6 Bollinger Reversion | `strategies/mean_reversion/bollinger.py` |
| 2.7 | A7 Z-Score Reversion | `strategies/mean_reversion/z_score.py` |
| 2.8 | A8 Range Expansion/Contraction | `strategies/range/expansion_contraction.py` |
| 2.9 | B1 Calendar DCA | `strategies/dca/calendar.py` |
| 2.10 | B2 Value Averaging | `strategies/dca/value_averaging.py` |
| 2.11 | B3 Volatility-Adjusted DCA | `strategies/dca/volatility_adj.py` |
| 2.12 | B4 Indicator-Triggered DCA | `strategies/dca/indicator_triggered.py` |
| 2.13 | B7 Drawdown-Tier Accumulation | `strategies/dca/drawdown_tier.py` |
| 2.14 | C1 Periodic Rebalancing | `strategies/rebalance/periodic.py` |
| 2.15 | C2 Threshold Rebalancing | `strategies/rebalance/threshold.py` |
| 2.16 | C3 Risk Parity | `strategies/rebalance/risk_parity.py` |
| 2.17 | C4 HODL++ (composite) | `strategies/hybrid/hodl_plus_plus.py` |
| 2.18 | D1 MA Crossover | `strategies/trend/ma_crossover.py` |
| 2.19 | D2 Donchian Breakout | `strategies/trend/donchian.py` |
| 2.20 | D3 Volatility Breakout | `strategies/trend/volatility_breakout.py` |
| 2.21 | D4 Time-Series Momentum | `strategies/trend/time_series_momentum.py` |
| 2.22 | D5 Multi-TF Alignment | `strategies/trend/multi_tf_alignment.py` |
| 2.23 | E3 BTC Dominance Rotation | `strategies/rotation/btc_dominance.py` |
| 2.24 | F1 Halving Cycle Positioning | `strategies/cycle/halving_position.py` |
| 2.25 | G1 Volatility Targeting | `strategies/vol_regime/vol_targeting.py` |

### Supporting tasks (interleaved with strategy tasks)

### Task 2.26 — Backtest framework
- **RED:** historical kline replay produces realistic fills (slippage + fees), generates performance analytics
- **GREEN:** `src/trading_sandwich/backtest/` — replay engine + fill simulator + analytics
- Run backtests for each implemented strategy; document results
- **Commit:** `feat(backtest): historical replay engine for strategies`

### Task 2.27 — Portfolio strategist runtime/CLAUDE.md rewrite
- **RED:** snapshot test with required sections (HALAL warning, role definition, decision classes, MCP tool reference)
- **GREEN:** Replace `runtime/CLAUDE.md` per spec §3.7. Backup old to `runtime/CLAUDE.md.heartbeat-trader.bak`.
- New decision classes: SUPERVISE/ALERT/ADJUST/PAUSE/DEPLOY/WIND_DOWN/REGIME_OVERRIDE/CURATE/OBSERVE
- **Commit:** `feat(runtime): portfolio strategist CLAUDE.md`

### Task 2.28 — Update SOUL.md and GOALS.md
- **RED:** snapshot tests for required content
- **GREEN:** Rewrite SOUL.md (identity: portfolio strategist) + GOALS.md (mandate: allocate strategies). Backup originals.
- **Commit:** `feat(runtime): portfolio strategist SOUL + GOALS`

### Task 2.29 — Freeze discretionary trader path
- **RED:** `propose_trade` rejected unless called with `emergency_override=True`
- **GREEN:** Add policy gate to MCP `propose_trade` tool. signal-worker continues for analytics.
- **Commit:** `chore: freeze discretionary trader path (preserve for analytics)`

### Task 2.30 — First Wave 1 live deployment
- Operator approves via Discord. Claude deploys grid-btc-1 at $30, range from current regime classification, 5 levels.
- Monitor 7 days. Compare actual fills to backtest expectations.
- **Commit:** `chore: first Wave 1 live deployment — grid-btc-1`

### Task 2.31 — Wave 1 progressive rollout
- After 7-day monitoring of first deployment: roll out remaining Wave 1 strategies across full universe per Claude's allocation decisions.
- Discretionary trader switched to "supervisor only" mode (no propose_trade calls).
- Each new strategy goes live small first, then scales.
- **Commit:** `chore: Wave 1 progressive rollout`

### **CHECKPOINT 2** — Wave 1 stabilization review (30 days post-deployment)

Operator + Claude review Wave 1 metrics. Decision: proceed to Wave 2 only if Wave 1 success criteria from spec §10 are met.

---

## Phase 3 — Wave 2: Sentiment & Cross-sectional (Tasks 46–53, ~2 weeks)

**Goal:** 7 strategies needing CFGI feed + universe-wide performance ranking + sector basket data. CFGI integration + new strategies.

### Task 3.1 — CFGI feed integration
- **RED:** test CFGI fetcher returns valid value, handles API outage gracefully (cached fallback)
- **GREEN:** `src/trading_sandwich/data/cfgi.py` — alternative.me API client + Postgres-cached values + Celery beat refresh task
- **Commit:** `feat(data): CFGI feed integration with caching`

### Task 3.2 — Cross-sectional / basket performance computation
- **RED:** test universe-wide performance ranking computes correctly
- **GREEN:** `src/trading_sandwich/regime/cross_sectional.py` — ranks symbols by 7d/30d performance, computes sector baskets
- **Commit:** `feat(regime): cross-sectional performance ranking`

| Task | Strategy ID | File |
|---|---|---|
| 3.3 | B5 Fear & Greed Buying | `strategies/dca/fear_greed.py` |
| 3.4 | B10 Reverse DCA / Profit Ladders | `strategies/dca/profit_ladder.py` |
| 3.5 | G2 Anti-cyclical Deployment | `strategies/vol_regime/anti_cyclical.py` |
| 3.6 | E1 Cross-Sectional Momentum | `strategies/rotation/cross_sectional_momentum.py` |
| 3.7 | E2 Sector Rotation | `strategies/rotation/sector.py` |
| 3.8 | E4 Long-Only Pair Rotation | `strategies/rotation/pair_rotation.py` |
| 3.9 | E5 Index Tilt | `strategies/rotation/index_tilt.py` |

Each follows the standard strategy task pattern. Live deployment per Claude's allocation decisions.

### **CHECKPOINT 3** — Wave 2 stabilization review

---

## Phase 4 — Wave 3: On-chain & Cycle Detection (Tasks 54–60, ~3 weeks)

**Goal:** 5 strategies needing on-chain data feeds + multi-signal aggregation for cycle bottoms/tops.

### Task 4.1 — On-chain data feed integration
- **RED:** test Glassnode/CryptoQuant fetcher returns valid MVRV, NUPL values; handles outages
- **GREEN:** `src/trading_sandwich/data/onchain.py` — API client + Postgres-cached values + Celery beat refresh
- Operator decides whether to subscribe to paid tier (free tier sufficient for V1 strategies; paid tier needed for some signals)
- **Commit:** `feat(data): on-chain MVRV/NUPL feed integration`

| Task | Strategy ID | File |
|---|---|---|
| 4.2 | B6 MVRV/NUPL Mechanical | `strategies/dca/mvrv_nupl.py` |
| 4.3 | B8 Pre-Halving Window DCA | `strategies/dca/pre_halving.py` |
| 4.4 | B9 Capitulation Detection | `strategies/dca/capitulation.py` |
| 4.5 | F2 Cycle Bottom Detection | `strategies/cycle/bottom_detect.py` |
| 4.6 | F3 Cycle Top Detection | `strategies/cycle/top_detect.py` |

Each follows the standard strategy task pattern. Live deployment per Claude's allocation decisions.

### **CHECKPOINT 4** — Full system review

Phase 4 complete. Full ~37 strategy library deployed. Claude as portfolio strategist managing multi-strategy halal portfolio across full universe. Review:

- Combined PnL vs HODL benchmark
- Per-strategy contribution analysis
- Regime classifier accuracy retrospective
- Claude allocation decision quality (`portfolio_decisions` log review)
- Operational metrics (uptime, error rate, recovery from crashes)
- Universe utilization (which symbols ran which strategies)

---

## Failure modes and recovery

| Issue | Action |
|---|---|
| Test fails for non-obvious reason | Use `superpowers:systematic-debugging` skill. Don't paper over. |
| Strategy backtest results suspicious | Compare to academic baseline (DCA +202%, F&G contrarian +1,145%, Shrimpy 15% +77% over HODL). If you can't validate, escalate to operator. |
| Live strategy loses money beyond circuit breaker | Auto-pause. Notify operator via Discord severity=alert. Diary entry. Investigate before resuming. |
| Plan task feels wrong | STOP. Flag to operator. Don't restructure plan mid-execution. |
| Migration would lose data | STOP. Plans don't include destructive migrations. If you need one, write a new spec. |
| External feed (CFGI/on-chain) down | Strategy depending on it gracefully degrades; cached value used; Discord alert fires. |

---

## Out-of-band: when operator may want to override the plan

Operator may at any time:

- Pause execution to add scope (new strategy idea — but must fit into existing wave structure or trigger new wave)
- Reorder tasks within a wave (e.g., F&G buy before grid because of cycle position)
- Skip ahead with operator approval (e.g., implement B6 MVRV early if on-chain feed already integrated)
- Cancel discretionary trader earlier than planned
- Adjust capital allocation, universe, or policy thresholds

Each requires a plan amendment commit before execution continues. Don't silently deviate.

---

## Estimated calendar

| Phase | Wave | Tasks | Duration |
|---|---|---|---|
| Phase 1 | Wave 0 (Foundation) | 16 | 2 weeks |
| Phase 2 | Wave 1 (Self-contained, 25 strategies + supporting) | 31 | 3 weeks |
| Phase 3 | Wave 2 (Sentiment + cross-sectional, 7 strategies + 2 infra) | 9 | 2 weeks (after 30d Wave 1 stabilization) |
| Phase 4 | Wave 3 (On-chain + cycle, 5 strategies + 1 infra) | 6 | 3 weeks (after 30d Wave 2 stabilization) |
| **Total active build** | | **62 tasks** | **~10 weeks** |
| **Total wall-clock incl. soak periods** | | | **~15 weeks** |

---

## Final pre-flight before starting

When new session opens this plan:

1. ✅ Read spec in full
2. ✅ Confirm operator: capital, universe (per spec §6.1), Discord channels, halving timing, external feed approvals
3. ✅ Verify trading-mcp-sandwich infra alive: `docker compose ps` shows healthy
4. ✅ `git status` clean, `git log --oneline -5` shows expected last commits
5. ✅ Run `docker compose run --rm cli doctor` — all green
6. ✅ Begin Task 1.1 — branch creation

Then execute in order, one task per commit, with checkpoints honored.

---

*End of plan. Spec: `docs/superpowers/specs/2026-05-09-phase-3-strategy-pivot-design.md`*
*Project policy: `/CLAUDE.md`*
*Architecture: `/architecture.md`*
