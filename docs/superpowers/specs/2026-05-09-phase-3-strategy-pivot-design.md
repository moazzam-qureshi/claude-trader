# Phase 3 — Strategy Pivot: Portfolio Strategist (Design Spec)

> **Status:** Designed 2026-05-09; ready for plan execution.
> **Author:** Brainstormed with operator 2026-05-09 across long session covering halal trading constraints, retail-scale economics, full mechanical strategy catalog, and architectural pivot from discretionary trading.
> **Predecessors:**
>   - `2026-04-21-trading-sandwich-design.md` (Phase 0 — skeleton)
>   - `2026-04-24-phase-1-feature-stack.md` (Phase 1 — features)
>   - `2026-04-25-phase-2-claude-triage-design.md` (Phase 2 — signal-driven triage)
>   - `2026-04-26-heartbeat-trader-design.md` (Phase 2.7 — discretionary heartbeat trader)
>   - `2026-04-26-halal-spot-conversion.md` (halal conversion)
> **Architecture reference:** `/architecture.md` (MCP-Sandwich pattern)
> **Project policy reference:** `/CLAUDE.md`
> **Plan:** `docs/superpowers/plans/2026-05-09-phase-3-strategy-pivot.md`

---

## 1. Goal and non-goals

### 1.1 Goal

Pivot the trading system from **discretionary AI heartbeat trading** to **mechanical strategy trading with Claude as portfolio strategist**.

The discretionary heartbeat trader (Phase 2.7) failed in production with the following empirical pattern:

- Excessive OBSERVE outcomes (Claude over-rejecting setups under uncertainty)
- Loss-heavy realised PnL when trades did fire (taking crowded late patterns)
- Sample-size convergence problem — could not reach 50+ trades to validate win-rate calibration before regimes shifted

This is **not** a Claude-intelligence failure. It is a structural property of retail discretionary pattern trading: the patterns themselves have been arbitraged out at retail size, and adaptive position sizing on small samples is a math trap regardless of agent capability.

The pivot reassigns Claude from **discretionary trader** (timing individual trades — a role mismatched to LLM strengths and structurally edgeless at retail scale) to **portfolio strategist** (deciding which mechanical strategies to deploy on which symbols at what capital, and when to pivot — a role aligned to LLM strengths with real edge).

### 1.2 In scope (Phase 3)

1. **Strategy Engine** — 30+ mechanical strategies across 7 categories implemented as composable executors with shared base class, idempotent state, crash-safe operation
2. **Strategy Manager** — deterministic regime classifier (multi-signal aggregation), strategy ↔ regime compatibility map, pivot logic with hysteresis, performance tracker
3. **Portfolio Strategist persona** — rewritten `runtime/CLAUDE.md` as portfolio strategist; SOUL.md and GOALS.md updated for new role identity
4. **Active strategy MCP tools** — Claude commands strategies (`deploy_grid`, `deploy_dca`, `wind_down_strategy`, `adjust_allocation`, `set_grid_range`), not just observes
5. **Discord interactive controls** — slash commands (`/strategies list`, `/regime override`, `/backtest`), button components (Pause/Resume/Adjust/Investigate), modals (parameter editing)
6. **Heartbeat cadence shift** — from 15–240 min to 6–24 hour cycles + event-driven wakeups (regime shift, drawdown threshold, strategy decay alert)
7. **Universe expansion** — full halal candidate universe (~30 active coins + observation tier), not artificially limited to a handful of symbols (see §6 universe section)
8. **Migration path** — discretionary trader frozen but not deleted; mechanical strategies run in parallel during transition; switchover only after first wave of strategies proves out in production

### 1.3 Out of scope (deferred to future phases)

- ML/AI signal layer beyond the deterministic regime classifier
- Multi-exchange execution (Binance-only; adapter abstraction maintained)
- Pure DEX self-custody execution (Binance custody for trading float, manual sweeps to cold storage)
- Web dashboard (Discord remains the UI per UI/Backend Split rule)
- Auto-parameter tuning via reinforcement learning (manual via Discord modal for V1)
- Cross-asset (stocks/forex/commodities) — crypto-only

### 1.4 Locked decisions

These are settled. Changes require new spec, not in-session pivot.

| Decision | Value | Rationale |
|---|---|---|
| Stack | Python (extend existing trading-mcp-sandwich) | Reuses ccxt-pro, pandas-ta, TA-Lib, Postgres+pgvector, Redis+Celery+RedBeat, Alembic, MCP server |
| Codebase | Extend `trading-mcp-sandwich`, do not fork | 80% of needed infrastructure already exists and is production-tested |
| Hosting | Same VPS as current trading-mcp-sandwich | Avoids re-deriving infrastructure lessons |
| Halal enforcement | Maintained at adapter layer | `max_leverage: 1`, longs-only, no perps/margin/lending |
| Capital | ~$167 USDT live (current policy.yaml) | Verify with operator at session start; may have changed |
| Custody | Binance trading float + manual weekly cold-storage sweeps of profit/excess | Aligns with operator's "crypto for UX, not maximalist self-custody ideology" stance |
| Discretionary trader | Frozen, not deleted; runs in parallel during initial wave migration | Failsafe; rollback path; analytical value of comparison data |
| Strategy paradigm | Full library of ~37 strategies, all first-class, implementation in dependency-based waves (no artificial tier-gating) | "Roll in all strategies" per operator directive 2026-05-09 |
| Universe | Full halal candidate universe (~30 coins active, observation tier for monitored, excluded for haram/operator-blocked) | "All candidate coins in our platform" per operator directive 2026-05-09 |
| Claude role | Portfolio strategist (regime + selection + allocation), NOT discretionary trader | Aligns LLM to its strengths |
| Discord | Remains primary control surface, extended with interactive controls | UI/Backend Split rule maintained |

---

## 2. The complete strategy catalog

All strategies below are **first-class platform citizens**, no tier-gating. Implementation order is dependency-based (see §4 implementation waves) — strategies needing only existing feature data ship first; strategies needing new external feeds ship as those feeds are integrated. Operator can deploy any implemented strategy at any time.

Strategies organized by return mechanism for navigation.

### 2.1 Category A — Range / Volatility Capture

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| A1 | Standard Grid | Buy/sell ladder in defined range | RANGE_VOLATILE, RANGE_QUIET |
| A2 | Infinity Grid | Grid with no upper limit, captures uptrend drift | RANGE_VOLATILE + slight TREND_UP |
| A3 | Geometric Grid | Percentage-spaced ladder | Same as A1, better for low-priced alts |
| A4 | Reverse Grid | Sell from existing holdings on rise, rebuy dips | When already holding asset |
| A5 | RSI Mean Reversion | Buy RSI<30, sell RSI>70 | RANGE_VOLATILE |
| A6 | Bollinger Reversion | Buy lower band, sell upper | Stable vol regimes |
| A7 | Z-Score Reversion | Statistical deviation entries | Stable mean |
| A8 | Range Expansion/Contraction | Inverse-vol position sizing | Vol regime shifts |

### 2.2 Category B — Accumulation

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| B1 | Calendar DCA | Fixed $X buy weekly | Universal |
| B2 | Value Averaging | Target portfolio-value growth, dynamic contributions | Ranging markets |
| B3 | Volatility-Adjusted DCA | Larger contributions when vol high | Bear markets |
| B4 | Indicator-Triggered DCA | DCA only fires when RSI<30 daily | Trending or ranging |
| B5 | Fear & Greed Buying | CFGI<25 → aggressive accumulate | Sentiment extremes |
| B6 | MVRV/NUPL Mechanical | Buy MVRV<1, scale out MVRV>3 | Cycle bottoms/tops |
| B7 | Drawdown-Tier Accumulation | Tiered deploy at 30/50/65/80% from ATH | Bear markets |
| B8 | Pre-Halving Window DCA | Aggressive accumulation 12–18 months before halving | Cycle position dependent |
| B9 | Capitulation Detection | Multi-onchain bottom signals | Cycle bottoms |
| B10 | Reverse DCA / Profit Ladders | Gradual exits at CFGI>75 / euphoria | Cycle tops |

### 2.3 Category C — Rebalancing

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| C1 | Periodic Rebalancing | Calendar-based reset to target % | Universal |
| C2 | Threshold Rebalancing | Rebalance only on >X% drift (Shrimpy 15% sweet spot) | Universal |
| C3 | Risk Parity | Vol-weighted allocation | Universal |
| C4 | HODL++ (Grid + Rebalance) | Grid running on rebalanced base | Range-bound |

### 2.4 Category D — Trend Following (mechanical)

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| D1 | MA Crossover | Long MA50>MA200 (Golden Cross filter) | Trends |
| D2 | Donchian Breakout (Turtle) | Buy 20-day high, exit 10-day low | Strong trends |
| D3 | Volatility Breakout | Long on vol expansion in trend direction | Sudden trend starts |
| D4 | Time-Series Momentum | Long when above N-day MA, cash below | Trends |
| D5 | Multi-TF Alignment | Only long when 1D+4H+1H all bullish | Strong trends |

### 2.5 Category E — Cross-Sectional / Rotation

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| E1 | Cross-Sectional Momentum | Long top-N performers in basket, monthly rebalance | Bull / late-recovery |
| E2 | Sector Rotation | Rotate L1/DeFi/AI/DePIN baskets by relative momentum | All regimes |
| E3 | BTC Dominance Rotation | BTC.D rising → BTC heavy; falling → alts heavy | Cycle position |
| E4 | Long-Only Pair Rotation | When pair ratio extreme, rotate to underperformer | Mean-reverting pairs |
| E5 | Index Tilt | Hold halal index but tilt to signal-favored | Long-term |

### 2.6 Category F — Cycle-Aware

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| F1 | Halving Cycle Positioning | Mechanical capital deployment by cycle phase | Cycle-driven |
| F2 | Cycle Bottom Detection | Multi-signal bottom (MVRV+CFGI+volume capitulation) | Bear bottoms |
| F3 | Cycle Top Detection | Pi Cycle Top, MVRV>3, distribution signals | Bull peaks |

### 2.7 Category G — Volatility Regime

| ID | Strategy | Mechanism | Best regime |
|---|---|---|---|
| G1 | Volatility Targeting | Scale exposure inversely to vol | All regimes |
| G2 | Anti-cyclical Deployment | Add capital when others scared (vol+fear high) | Capitulation |

### 2.8 Anti-patterns (explicitly never build)

| ID | Strategy | Why never |
|---|---|---|
| Z1 | Martingale / double-down on losers | Math trap; blow-up vector |
| Z2 | Pure ML/time-series prediction | Overfitting; no edge at retail |
| Z3 | Discretionary pattern trading | Already failed in Phase 2.7 production |
| Z4 | Yield farming | Haram (riba) |
| Z5 | DeFi LP yield strategies | Haram (riba) |
| Z6 | Lending protocols (AAVE, COMP) | Haram (riba) |
| Z7 | Funding rate arbitrage | Haram (uses perps) |
| Z8 | Cash-and-carry basis trades | Haram (uses perps) |

---

## 3. Architecture

### 3.1 Conceptual layout

```
┌────────────────────────────────────────────────────────────────┐
│  CLAUDE — Portfolio Strategist (the brain)                      │
│  • Reads market state + regime signals + performance            │
│  • Decides which strategies run, where, with how much capital   │
│  • Pivots on regime change                                      │
│  • Tunes parameters when strategies drift                       │
│  • Curates universe                                             │
└─────────────────────────┬──────────────────────────────────────┘
                          │ commands (via MCP tools)
                          ▼
┌────────────────────────────────────────────────────────────────┐
│  STRATEGY MANAGER (deterministic supporting layer)              │
│  • Regime classifier (ADX + ATR + MA structure + BB + on-chain) │
│  • Strategy ↔ regime compatibility map                          │
│  • Pivot hysteresis logic                                       │
│  • Performance tracker (per-strategy, per-regime expectations)  │
│  • Decision log (every classification + pivot recorded)         │
└─────────────────────────┬──────────────────────────────────────┘
                          │ deploy / pause / adjust
                          ▼
┌────────────────────────────────────────────────────────────────┐
│  STRATEGY ENGINE (the execution arms — mechanical executors)    │
│  • base.py (Strategy ABC + state machine + idempotency)         │
│  • grid.py / geometric_grid.py / infinity_grid.py / reverse_grid│
│  • dca/calendar.py / value_averaging.py / volatility_adj.py     │
│  • dca/indicator_triggered.py / fear_greed.py / drawdown_tier.py│
│  • dca/mvrv_nupl.py / pre_halving.py / profit_ladder.py         │
│  • rebalance/threshold.py / periodic.py / risk_parity.py        │
│  • trend/ma_crossover.py / donchian.py / multi_tf.py            │
│  • rotation/cross_sectional_momentum.py / btc_dominance.py      │
│  • cycle/halving_position.py / bottom_detect.py / top_detect.py │
│  • mean_reversion/rsi.py / bollinger.py / z_score.py            │
│  • vol_regime/anti_cyclical.py / vol_targeting.py               │
└─────────────────────────┬──────────────────────────────────────┘
                          │ orders
                          ▼
┌────────────────────────────────────────────────────────────────┐
│  EXECUTION-WORKER (existing — halal-enforced adapter)           │
│  ccxt-pro Binance, idempotent client IDs, rate-limited          │
└────────────────────────────────────────────────────────────────┘

CROSS-CUTTING (existing infrastructure, reused):
  • Postgres + pgvector + Alembic migrations
  • Redis + Celery + RedBeat (job queue + scheduler)
  • Discord listener + webhook + approval flow
  • Prometheus + Grafana
  • Pino-equivalent structured logging via stdlib + custom formatters
  • policy.yaml for runtime parameters
  • Git-versioned prompts (every Claude shift records HEAD hash)
```

### 3.2 Strategy Engine — base contract

Every strategy implements:

```python
class Strategy(ABC):
    @abstractmethod
    def tick(self, market_snapshot: MarketSnapshot) -> list[OrderIntent]:
        """Compute orders to place on this tick. Idempotent."""

    @abstractmethod
    def graceful_shutdown(self) -> list[OrderIntent]:
        """Cancel pending orders, keep filled positions, prepare for handoff."""

    @abstractmethod
    def emergency_stop(self) -> list[OrderIntent]:
        """Cancel everything, market-sell positions if directed."""

    @abstractmethod
    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        """Used by performance tracker to flag underperformance."""
```

State persists in `strategies` and `strategy_state` tables. Strategies are stateless workers — every tick reads state from DB, computes intent, writes back. Crash-safe; can be killed and restarted at any tick.

### 3.3 Strategy Manager — regime classifier (minimum viable)

```python
class Regime(Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE_VOLATILE = "range_volatile"
    RANGE_QUIET = "range_quiet"
    TRANSITIONING = "transitioning"

def classify_regime(symbol: str, timeframe: str = "4h") -> Regime:
    adx = get_adx(symbol, timeframe)
    atr_pct = get_atr(symbol, timeframe) / get_price(symbol)
    ma50 = get_sma(symbol, timeframe, 50)
    ma200 = get_sma(symbol, timeframe, 200)
    price = get_price(symbol)

    if adx > 25 and price > ma50 > ma200 and ma50_slope > 0:
        return Regime.TREND_UP
    if adx > 25 and price < ma50 < ma200 and ma50_slope < 0:
        return Regime.TREND_DOWN
    if adx < 20 and atr_pct > 0.03:
        return Regime.RANGE_VOLATILE
    if adx < 20 and atr_pct < 0.015:
        return Regime.RANGE_QUIET
    return Regime.TRANSITIONING
```

Hysteresis: pivot only after **2 consecutive** same classifications on the same timeframe. Prevents flip-flopping on a single 4h candle.

Manual override via `override_regime(symbol, regime, duration_hours)` MCP tool — Claude or operator can force a regime call.

### 3.4 Strategy ↔ Regime compatibility map

Stored in `policy.yaml` as declarative config:

```yaml
strategy_regime_compatibility:
  grid_standard: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]
  grid_infinity: [RANGE_VOLATILE, TREND_UP]
  dca_calendar: ["*"]              # always on
  dca_indicator: [TREND_DOWN, RANGE_VOLATILE]
  dca_fear_greed: ["*"]            # event-driven
  dca_drawdown_tier: ["*"]         # event-driven
  rebalance_threshold: ["*"]       # always on
  trend_ma_crossover: [TREND_UP]
  rotation_btc_dominance: ["*"]    # always on, slow cadence
  # ... full map for all strategies
```

### 3.5 New active MCP tools (Claude commands strategies)

```python
# Strategy lifecycle
deploy_strategy(strategy_type: str, symbol: str, capital_usd: float, params: dict) -> StrategyId
wind_down_strategy(strategy_id: str, urgency: Literal["graceful", "immediate"]) -> Status
pause_strategy(strategy_id: str, reason: str) -> Status
resume_strategy(strategy_id: str) -> Status
adjust_allocation(strategy_id: str, new_capital_usd: float) -> Status
adjust_params(strategy_id: str, params: dict) -> Status

# Strategy-specific helpers (sugar)
deploy_grid(symbol, capital_usd, low, high, levels, mode="standard")
deploy_dca(symbol, capital_usd, schedule, indicator_filter=None, fear_greed_threshold=None)
deploy_rebalancer(target_allocation: dict, threshold_pct: float)
deploy_trend_filter(symbol, capital_usd, ma_fast, ma_slow)
deploy_rotation(basket_type: str, capital_usd: float, lookback_days: int)
adjust_grid_range(strategy_id, low, high)

# Read tools
list_strategies(active_only=True) -> list[StrategyInfo]
get_strategy_performance(strategy_id, since: str = "7d") -> PerformanceReport
get_account_allocation() -> AllocationSnapshot
get_regime_signals(symbol: str) -> RegimeSnapshot
override_regime(symbol: str, regime: Regime, duration_hours: int, reason: str)

# Plus all existing tools remain available
# (get_open_positions, get_universe, mutate_universe, append_diary, write_state,
#  notify_operator, propose_trade [reserved for emergencies], etc.)
```

### 3.6 Discord interactive controls

**Slash commands** (operator → system):

```
/strategies list                  - active strategies + status
/strategies pause <id>            - pause strategy
/strategies resume <id>           - resume strategy
/strategies adjust <id>           - opens modal for params
/regime override <symbol>         - opens modal for regime override
/backtest <strategy> <symbol>     - spawns backtest oneshot
/equity                           - account value + allocation breakdown
/decisions last <duration>        - decision log
/sweep --to-cold-storage          - manual cold-storage withdrawal
```

**Button components** on every notification:

- Strategy fill notification: `[View P&L] [Cancel Pair] [Adjust Range]`
- Regime change pending: `[Confirm Pivot] [Block Pivot 24h] [Wait Hysteresis]`
- Underperformance alert: `[Pause] [Adjust] [Migrate Capital] [Investigate]`
- Daily summary: `[Detailed Report] [Holdings] [Decision Log]`
- Strategy decay alert: `[Wind Down] [Tune Params] [Force Hold]`

**Modal dialogs** for parameter editing — Discord native modals via discord.py.

### 3.7 Updated runtime/CLAUDE.md persona

Full rewrite. Key shifts from heartbeat-trader version:

- **Identity**: "You are the Portfolio Strategist for a halal spot trading system. You do NOT make individual trades. Strategies make trades mechanically. You decide which strategies run."
- **Cadence**: shifts every 6–24 hours (not 15–240 min). Event-driven wakeups for regime shifts, drawdowns, strategy decay.
- **Decision classes**: SUPERVISE | ALERT | ADJUST | PAUSE | DEPLOY | WIND_DOWN | REGIME_OVERRIDE | CURATE | OBSERVE
- **Information surface**: full strategy state, regime signals, performance vs expectations, full account allocation, decision history
- **Constraint**: cannot place orders directly via Binance; can only command strategies via MCP tools (which then place orders). Same architectural rule as Phase 2.7.

### 3.8 Heartbeat cadence

- **Daily review** at fixed UTC time (e.g., 06:00) — regime + performance
- **Event-driven wakeups**:
  - Regime shift candidate detected (1st classification confirmed, hysteresis pending)
  - Strategy hits drawdown threshold (>5% / 30 days against expectation)
  - Major price move (>5% on BTC in <1 hour)
  - Operator manually triggered via Discord
- **Estimated load**: 1–5 Claude shifts/day average (vs 10–48 in Phase 2.7) — much cheaper

---

## 4. Implementation waves (dependency-based, NOT tier-gated)

The full strategy library ships as a complete platform. Implementation order is purely **what data dependencies a strategy requires**. Strategies in earlier waves don't gate later strategies — operator can deploy any implemented strategy at any time. Waves describe build sequence, not feature availability.

### Wave 0 — Foundation (~2 weeks)
Platform infrastructure. Blocks everything else. Tasks 1–15 of plan.

- Strategy ABC + state machine + persistence
- Regime classifier + compatibility map + performance tracker
- All strategy MCP tools (read + active commands)
- Discord slash commands + buttons + modals
- strategy-worker Celery service
- Migrations (0013, 0014, 0015)

### Wave 1 — Self-contained strategies (~3 weeks, ~25 strategies)
Strategies that need only the existing feature stack (RSI, ATR, ADX, Bollinger Bands, EMA/SMA, price/volume — already computed in trading-mcp-sandwich Phase 1 features).

| Strategy | Notes |
|---|---|
| A1 Standard Grid | |
| A2 Infinity Grid | |
| A3 Geometric Grid | |
| A4 Reverse Grid | |
| A5 RSI Mean Reversion | |
| A6 Bollinger Reversion | |
| A7 Z-Score Reversion | |
| A8 Range Expansion/Contraction | |
| B1 Calendar DCA | |
| B2 Value Averaging | |
| B3 Volatility-Adjusted DCA | |
| B4 Indicator-Triggered DCA | |
| B7 Drawdown-Tier Accumulation | rolling ATH from existing kline data |
| C1 Periodic Rebalancing | |
| C2 Threshold Rebalancing | |
| C3 Risk Parity | |
| C4 HODL++ | composite of grid + rebalance |
| D1 MA Crossover | |
| D2 Donchian Breakout | |
| D3 Volatility Breakout | |
| D4 Time-Series Momentum | |
| D5 Multi-TF Alignment | |
| E3 BTC Dominance Rotation | BTC.D from existing TradingView MCP |
| F1 Halving Cycle Positioning | calendar-based |
| G1 Volatility Targeting | |

Portfolio strategist runtime/CLAUDE.md persona shift happens at end of Wave 1 (after strategies exist for Claude to command).

### Wave 2 — Sentiment & cross-sectional (~2 weeks, ~7 strategies)
Strategies needing CFGI feed + universe-wide performance ranking + sector basket data.

| Strategy | New dependency |
|---|---|
| B5 Fear & Greed Buying | CFGI feed (alternative.me API) |
| B10 Reverse DCA / Profit Ladders | CFGI feed |
| G2 Anti-cyclical Deployment | CFGI + vol |
| E1 Cross-Sectional Momentum | universe performance ranking |
| E2 Sector Rotation | sector basket performance |
| E4 Long-Only Pair Rotation | cointegration analysis |
| E5 Index Tilt | |

### Wave 3 — On-chain & cycle detection (~3 weeks, ~5 strategies)
Strategies needing on-chain data feeds (Glassnode, CryptoQuant) and multi-signal aggregation.

| Strategy | New dependency |
|---|---|
| B6 MVRV/NUPL Mechanical | Glassnode/CryptoQuant API |
| B8 Pre-Halving Window DCA | calendar + adaptive sizing |
| B9 Capitulation Detection | multi-onchain bottom signals |
| F2 Cycle Bottom Detection | multi-signal aggregation |
| F3 Cycle Top Detection | Pi Cycle Top, MVRV>3 |

### Cutover plan (parallel-running, never broken)

- **Week 1–2 (Wave 0):** foundation in DRY-RUN. Discretionary trader (Phase 2.7) continues unchanged.
- **Week 3:** portfolio strategist runtime/CLAUDE.md replaces heartbeat trader. Operator approves each first deployment via Discord.
- **Week 4:** first Wave 1 strategy live (Grid on BTC, $30 capital). Monitor 7 days. Discretionary trader still running.
- **Week 5–6:** Wave 1 strategies progressively live across full universe. Discretionary trader switched to supervisor-only mode.
- **End of Wave 1:** discretionary trader fully retired. Code preserved frozen for analytics.
- **Wave 2 begins** as CFGI feed integrated. Each strategy goes live small, then scales.
- **Wave 3 begins** as on-chain feeds integrated.

**At no point is the system broken.** Discretionary trader runs until first Wave 1 strategies prove out. Every new strategy goes live small first. Rollback path always available.

---

## 5. Database schema additions

Three new migrations:

### 5.1 `0013_strategies.py`

```sql
CREATE TABLE strategies (
    id BIGSERIAL PRIMARY KEY,
    strategy_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,  -- active, paused, winding_down, completed, errored
    capital_allocated_usd NUMERIC NOT NULL,
    capital_deployed_usd NUMERIC NOT NULL,
    params JSONB NOT NULL,
    deployed_by TEXT NOT NULL,  -- 'claude' | 'operator' | 'system'
    deployed_at TIMESTAMPTZ NOT NULL,
    last_tick_at TIMESTAMPTZ,
    paused_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    prompt_version TEXT,  -- git hash if deployed by Claude
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_type, symbol, status) WHERE status IN ('active', 'paused')
);

CREATE TABLE strategy_state (
    strategy_id BIGINT PRIMARY KEY REFERENCES strategies(id),
    state JSONB NOT NULL,  -- strategy-specific state
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE strategy_orders (
    id BIGSERIAL PRIMARY KEY,
    strategy_id BIGINT NOT NULL REFERENCES strategies(id),
    order_id BIGINT NOT NULL REFERENCES orders(id),
    role TEXT NOT NULL,  -- 'entry', 'exit', 'rebalance', etc.
    grid_level INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.2 `0014_regime_classifications.py`

```sql
CREATE TABLE regime_classifications (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    regime TEXT NOT NULL,
    signals JSONB NOT NULL,  -- adx, atr_pct, ma_structure, etc.
    classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_regime_classifications_symbol_classified_at
    ON regime_classifications(symbol, classified_at DESC);

CREATE TABLE regime_pivots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    from_regime TEXT,
    to_regime TEXT NOT NULL,
    triggered_by TEXT NOT NULL,  -- 'classifier_hysteresis' | 'claude_override' | 'operator_override'
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actions_taken JSONB NOT NULL,  -- which strategies were affected
    prompt_version TEXT
);
```

### 5.3 `0015_portfolio_decisions.py`

```sql
CREATE TABLE portfolio_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_type TEXT NOT NULL,  -- 'deploy' | 'wind_down' | 'pause' | 'resume' | 'adjust' | 'override'
    target_strategy_id BIGINT REFERENCES strategies(id),
    target_symbol TEXT,
    rationale TEXT NOT NULL,
    market_context JSONB,  -- snapshot of relevant signals at decision time
    decided_by TEXT NOT NULL,  -- 'claude' | 'operator' | 'auto'
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prompt_version TEXT
);
```

---

## 6. policy.yaml additions

### 6.1 Universe expansion (full halal candidate set)

Replaces existing `universe.tiers` block in policy.yaml. Operator directive 2026-05-09: "all candidate coins in our platform."

```yaml
universe:
  tiers:
    core:
      symbols: [BTCUSDT, ETHUSDT, SOLUSDT]
      size_multiplier: 1.0
      max_concurrent_positions: 4
      shift_attention: every_shift

    active:
      # Halal-clean L1s, L2s, DePIN, AI, currency. Strategies enabled.
      symbols: [
        # L1s
        AVAXUSDT, ADAUSDT, NEARUSDT, APTUSDT, SUIUSDT,
        ATOMUSDT, ALGOUSDT, DOTUSDT,
        # L2s / scaling
        ARBUSDT, OPUSDT, POLUSDT, IMXUSDT, STRKUSDT,
        # DePIN / infra
        LINKUSDT, FILUSDT, RNDRUSDT, GRTUSDT,
        # AI
        TAOUSDT, FETUSDT, WLDUSDT,
        # Currency
        LTCUSDT, BCHUSDT,
      ]
      size_multiplier: 0.5
      max_concurrent_positions: 12
      shift_attention: time_permitting

    observation:
      # Smaller liquidity / newer / monitored. No auto-trading; can be promoted.
      symbols: [HNTUSDT, AKTUSDT, AGIXUSDT, OCEANUSDT, DASHUSDT, ZECUSDT, INJUSDT]
      size_multiplier: 0.0
      max_concurrent_positions: 0
      shift_attention: weekly_sweep

    excluded:
      # Lending/yield protocols (riba)
      symbols_lending: [AAVEUSDT, COMPUSDT, MKRUSDT, LDOUSDT, CRVUSDT, CAKEUSDT]
      # Perpetual futures protocols (haram structure)
      symbols_perp_protocols: [GMXUSDT, DYDXUSDT, GNSUSDT]
      # Memecoins (operator policy — gambling/maysir concerns + uncorrelated vol)
      symbols_memecoins: [SHIBUSDT, PEPEUSDT, BONKUSDT, WIFUSDT, FLOKIUSDT, DOGEUSDT]
      reason: "haram (riba on lending; perp structure on derivatives) or operator policy (memecoins)"

  hard_limits:
    min_24h_volume_usd_floor: 40000000
    vol_30d_annualized_max_ceiling: 3.00
    excluded_symbols_locked: [
      SHIBUSDT, PEPEUSDT, BONKUSDT, WIFUSDT, FLOKIUSDT, DOGEUSDT,
      AAVEUSDT, COMPUSDT, MKRUSDT, LDOUSDT, CRVUSDT, CAKEUSDT,
      GMXUSDT, DYDXUSDT, GNSUSDT
    ]
    core_promotions_operator_only: true
    max_total_universe_size: 40
    max_per_tier:
      core: 5
      active: 25
      observation: 15
```

**Universe rationale:**
- **Core (3 symbols):** highest liquidity, mainstream halal acceptance, full sizing
- **Active (~22 symbols):** halal-clean utility tokens with adequate liquidity (>$40M daily). Strategies enabled at 0.5x sizing.
- **Observation (~7 symbols):** monitored but not auto-traded. Promotable to active by Claude per existing universe-mutation logic.
- **Excluded (~16 symbols):** structurally haram (lending, perps, yield protocols) or operator-policy excluded (memecoins). Locked — Claude cannot un-exclude.

Halal status verified per scholar consensus (Mufti Faraz Adam, Joe Bradford, Sheikh Yasir Qadhi for crypto-specific rulings). Excluded set explicitly enumerated to prevent ambiguity.

### 6.2 Strategy configuration

```yaml
strategies:
  enabled: true

  # Per-strategy deployment caps and defaults
  grid_standard:
    max_active_per_symbol: 1
    default_levels: 5
    default_range_atr_multiplier: 2.0
  grid_infinity:
    max_active_per_symbol: 1
    default_levels: 5
  grid_geometric:
    max_active_per_symbol: 1
    default_levels: 6
    default_pct_spacing: 0.02
  dca_calendar:
    default_schedule_cron: "0 0 * * MON"
    default_amount_usd: 10
  dca_indicator:
    rsi_threshold: 30
    rsi_timeframe: "1d"
  dca_fear_greed:
    aggressive_threshold: 25  # CFGI below this = aggressive accumulate
    pause_threshold: 75       # CFGI above this = pause buys
  dca_drawdown_tier:
    tiers: [0.30, 0.50, 0.65, 0.80]
    tier_size_multipliers: [0.10, 0.20, 0.30, 0.40]
    rolling_ath_window_days: 90
  rebalance_threshold:
    default_threshold_pct: 0.15
    default_target_allocation:
      BTCUSDT: 0.40
      ETHUSDT: 0.20
      SOLUSDT: 0.15
      LINKUSDT: 0.10
      AVAXUSDT: 0.05
      cash: 0.10
  rotation_btc_dominance:
    btc_d_high_threshold: 55
    btc_d_low_threshold: 45
    rotation_lookback_days: 14
  cycle_halving:
    halving_dates: ["2024-04-19", "2028-04-15"]  # actual + estimate
    pre_halving_window_months: 18
    post_halving_window_months: 12

# Strategy-regime compatibility (declarative — full map for all strategies)
strategy_regime_compatibility:
  # Range capture (Category A)
  grid_standard: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]
  grid_infinity: [RANGE_VOLATILE, TREND_UP]
  grid_geometric: [RANGE_VOLATILE, RANGE_QUIET]
  grid_reverse: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]
  rsi_mean_reversion: [RANGE_VOLATILE]
  bollinger_reversion: [RANGE_VOLATILE, RANGE_QUIET]
  z_score_reversion: [RANGE_VOLATILE, RANGE_QUIET]
  range_expansion_contraction: [RANGE_VOLATILE, RANGE_QUIET]
  # Accumulation (Category B)
  dca_calendar: ["*"]
  dca_value_averaging: ["*"]
  dca_volatility_adj: ["*"]
  dca_indicator: [TREND_DOWN, RANGE_VOLATILE]
  dca_fear_greed: ["*"]
  dca_mvrv_nupl: ["*"]
  dca_drawdown_tier: ["*"]
  dca_pre_halving: ["*"]
  dca_capitulation: [TREND_DOWN]
  dca_profit_ladder: [TREND_UP]
  # Rebalancing (Category C)
  rebalance_periodic: ["*"]
  rebalance_threshold: ["*"]
  rebalance_risk_parity: ["*"]
  hodl_plus_plus: [RANGE_VOLATILE, RANGE_QUIET, TREND_UP]
  # Trend (Category D)
  trend_ma_crossover: [TREND_UP]
  trend_donchian: [TREND_UP, TREND_DOWN]
  trend_volatility_breakout: [RANGE_QUIET, TREND_UP]
  trend_time_series_momentum: [TREND_UP]
  trend_multi_tf_alignment: [TREND_UP]
  # Rotation (Category E)
  rotation_cross_sectional: [TREND_UP, RANGE_VOLATILE]
  rotation_sector: ["*"]
  rotation_btc_dominance: ["*"]
  rotation_pair: [RANGE_VOLATILE]
  rotation_index_tilt: ["*"]
  # Cycle-aware (Category F)
  cycle_halving: ["*"]
  cycle_bottom_detection: [TREND_DOWN]
  cycle_top_detection: [TREND_UP]
  # Volatility regime (Category G)
  vol_targeting: ["*"]
  vol_anti_cyclical: ["*"]

# Regime classifier params
regime_classifier:
  primary_timeframe: "4h"
  hysteresis_required_consecutive: 2
  adx_trend_threshold: 25
  adx_range_threshold: 20
  atr_pct_volatile_threshold: 0.03
  atr_pct_quiet_threshold: 0.015
  manual_override_max_duration_hours: 168  # 1 week max

# Performance tracker (alert thresholds)
performance_tracker:
  alert_drawdown_pct: 0.05
  alert_underperform_vs_expected_pct: 0.50  # if actual <50% of expected
  evaluation_window_days: 30
```

---

## 7. Discretionary trader migration

The Phase 2.7 heartbeat trader is **frozen**, not deleted. Specifically:

| Component | Action |
|---|---|
| `triage-worker` (heartbeat trader) | Repurposed as portfolio strategist (same worker, new runtime/CLAUDE.md) |
| `signal-worker` (archetype detection) | Continue running for analytics; signals table becomes read-only data source for Claude's context, no longer drives trades |
| `signals` table | Continue ingesting; no writes from new path |
| `claude_decisions` table | Frozen (no new writes from old path); preserved for analytics |
| `propose_trade` MCP tool | Reserved for emergency manual operator override only; not part of normal portfolio strategist flow |
| Old runtime/CLAUDE.md | Backed up to `runtime/CLAUDE.md.heartbeat-trader.bak` for reference |

This preserves analytical value (we can compare what discretionary signals would have done vs what mechanical strategies actually did) while removing them from the live trade path.

---

## 8. Open questions for session start

These need operator confirmation before plan execution begins:

1. **Capital still ~$167 USDT?** policy.yaml is dated 2026-04-26; verify current value.
2. **Universe:** confirm full universe per §6.1 (3 core + ~22 active + ~7 observation + ~16 excluded = ~32 active candidates). Any symbols to add/remove from operator's halal scholar review?
3. **Halving 2028 timing:** if confirmed for ~Apr 2028, pre-halving aggressive accumulation window (B8) starts ~Sep 2026 — implies B8 needs to be live before then (Wave 3 dependency).
4. **Discord webhook URLs:** existing `DISCORD_UNIVERSE_WEBHOOK_URL` reused, or separate channels for strategy notifications vs universe events?
5. **External data feeds:** approval for adding feeds — alternative.me CFGI (free), Glassnode/CryptoQuant (paid, optional for Wave 3) — operator decides whether to subscribe.

---

## 9. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Mechanical strategies underperform in live trading vs backtest | Medium | High | DRY-RUN week 1; live with small capital ($30) per strategy; parallel-run discretionary trader as fallback |
| Regime classifier mis-classifies, causing inappropriate strategy deployment | Medium | Medium | Hysteresis (2-consecutive requirement) + Claude review + manual override path |
| Strategy state corruption during crash | Low | Medium | Idempotent ticks, state in DB not memory, every tick can be safely retried |
| Discord controls misused / accidental commands | Low | Medium | Operator-only access (Discord role check), confirmation modals for destructive actions, audit log |
| Claude makes poor allocation decisions | Medium | Medium | Operator approval required for first deployment of each strategy; Claude proposes, operator confirms via Discord button for first wave deployments |
| Policy.yaml drift between code and config | Low | High | Pydantic validation at startup; `cli doctor` validates config integrity |
| Strategy bugs cause loss | Medium | High | TDD throughout; backtest before live; small capital per strategy; circuit breaker (auto-pause if drawdown > threshold) |
| Universe too large to manage at $167 capital | Medium | Low | Universe defines what's TRADEABLE; Claude allocates based on available capital. ~30 candidates, ~5-15 strategies active simultaneously, capital constrains naturally. |

---

## 10. Success criteria

### Wave 1 success (30 days after first Wave 1 strategy goes live)

1. ✅ All deployed strategies run continuously without crashes (>99% uptime)
2. ✅ Regime classifier produces stable classifications (no flip-flopping)
3. ✅ Claude makes at least 5 portfolio strategist decisions logged with rationale
4. ✅ Combined deployed strategies' realized PnL is non-negative (no requirement to beat HODL — just don't lose money mechanically)
5. ✅ Operator can pause/adjust any strategy via Discord in <10 seconds
6. ✅ All decisions traceable to git-versioned prompt + policy.yaml version
7. ✅ Discretionary trader fully retired without disruption

### Wave 2 success (30 days after Wave 2 strategies live)

1. ✅ CFGI feed integrated with <1% downtime; cached locally for resilience
2. ✅ Cross-sectional momentum and rotation strategies deployable on the full universe
3. ✅ Combined Wave 1 + Wave 2 PnL beats HODL benchmark on at least one symbol

### Wave 3 success (30 days after Wave 3 strategies live)

1. ✅ On-chain feeds integrated; degrade gracefully if feeds fail
2. ✅ Cycle bottom/top detection produces actionable signals (verifiable in retrospect)
3. ✅ Full ~37 strategy library available; Claude exercises full allocation discretion across categories

---

## Amendments (recorded during execution)

### A1 — strategy_orders.order_id type (2026-05-10, Task 1.2)

Spec §5.1 declared:

```sql
CREATE TABLE strategy_orders (
    ...
    order_id BIGINT NOT NULL REFERENCES orders(id),
    ...
);
```

The existing `orders` table (migration 0010) actually uses
`orders.order_id UUID PRIMARY KEY`. There is no `orders.id` column.
Migration 0013 implements the FK as
`order_id UUID NOT NULL REFERENCES orders(order_id)` to match reality.
Semantic intent is preserved — it links a strategy-placed order to the
canonical orders row. No code outside Phase 3 affected.

---

*End of design spec. Plan: `docs/superpowers/plans/2026-05-09-phase-3-strategy-pivot.md`*
