# Trading Sandwich — Phase 1: Full Feature Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow the Phase 0 skeleton into a signal generator with ~25 indicators, 8 archetypes, rule-based regime classification, all 6 outcome horizons, and top-8 × 5-timeframe coverage — ready for Claude triage in Phase 2.

**Architecture:** Existing 4-worker layout (ingestor + feature + signal + outcome) scaled horizontally and extended. New raw tables for order-book snapshots, funding, open-interest, long/short ratio. pgbouncer fronts Postgres, `celery-redbeat` persists multi-day countdowns, `raw_candles` becomes month-partitioned. All indicator math lives in a new `indicators/` package (one module per family) and regime classification in `regime/classifier.py`. Eight detectors, each a pure function, live under `signals/detectors/` and are iterated by the signal worker. A three-stage gating chain (threshold → cooldown → dedup) replaces Phase 0's two-stage gate.

**Tech Stack additions over Phase 0:** TA-Lib (pinned Debian package), pgbouncer, pgbouncer_exporter, celery-redbeat, pg_partman (or hand-rolled declarative partitioning).

**Reference:** Spec at `docs/superpowers/specs/2026-04-24-phase-1-feature-stack.md`. Phase 0 plan at `docs/superpowers/plans/2026-04-21-phase-0-skeleton.md` (as-built reference for conventions). Pattern at `architecture.md`.

---

## Handoff to Next Session

**Status on handoff:** Phase 0 shipped. `main` currently at commit tagged `phase-0-complete` (or the commit right after Task 28's smoke, whichever the user prefers). Full Phase 0 test suite green (37 tests). Phase 1 spec committed at `80ce341` or later.

**Start here in the next session:**
1. Read the spec at `docs/superpowers/specs/2026-04-24-phase-1-feature-stack.md` in full.
2. Read this plan in full. Phase 0's plan remains authoritative on how to run commands (Docker-only, `docker compose run --rm test …`, `docker compose run --rm tools …`).
3. Execute Task 1, then proceed task-by-task. Every task has a RED → GREEN → commit cycle except pure-infra tasks (marked).
4. Pause for human review at the six checkpoints listed under "Checkpoints" below.

**Authoritative decisions already locked (do not re-litigate):**
- 8 archetypes (Phase 0's `trend_pullback` + `squeeze_breakout`, `divergence_rsi`, `divergence_macd`, `range_rejection`, `liquidity_sweep_daily`, `liquidity_sweep_swing`, `funding_extreme`).
- Rule-based regime classification. ML deferred until ≥10k outcome rows per regime label exist.
- Top-8 symbols (BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX) × 5 timeframes (5m, 15m, 1h, 4h, 1d). 1m is dropped.
- Full backfill: 1 year of REST-fetched raw candles for every (symbol, TF) before Phase 1 features backfill runs.
- pgbouncer (session-pool mode, port 6432); Alembic migrations + features-backfill tool bypass pgbouncer and hit Postgres directly.
- `celery-redbeat` replaces the in-memory Beat scheduler.
- `raw_candles` becomes declaratively-partitioned by month on `open_time`.
- Dedup gate: strictly-higher-timeframe signal suppresses lower-timeframe signals for the same (symbol, direction) within `dedup_window_minutes`.

**If the agent hits friction:**
- **TA-Lib source build is banned.** Use the Debian package (`libta-lib0`, `libta-lib-dev`). If the Debian version isn't 0.6.x or later, pin a specific Debian snapshot; do not fall back to SourceForge.
- **pgbouncer + Alembic:** Alembic connects to `postgres:5432` directly, not `pgbouncer:6432`. If you see `cannot handle SQL statements at this level`, that's pgbouncer rejecting Alembic's DDL — the connection is wrong.
- **pytest-asyncio + testcontainers:** Phase 0 solved the "asyncio.run() inside running loop" issue by making integration tests sync. Keep them sync. Use the `env_for_postgres` and `env_for_redis` fixtures from Phase 0's `tests/conftest.py`.
- **Eager-mode Celery** propagates stale broker/producer caches across tests. Phase 0 solved this by popping `app._pool` and `app.__dict__["amqp"]` in `_reset_module_singletons`. This MUST work in Phase 1 too — do not regress it. See `tests/conftest.py`.
- **OB-imbalance** depends on a new `raw_orderbook_snapshots` table. If the feature-worker's new `microstructure.py` module fails to join on candle close, check that the depth ingestor is actually writing at ≥5/sec per symbol. Empty `raw_orderbook_snapshots` → `ob_imbalance_05` NULL → all features rows have NULL in that column → acceptable but flagged in the regression test.

**Checkpoints (pause for human review):**
- **Checkpoint F** after Task 10: deps + infra (TA-Lib, pgbouncer, redbeat, docker-compose updates) runnable; compose config parses; unit suite still green.
- **Checkpoint G** after Task 20: schema migrations 0003–0009 applied; contracts expanded; new raw tables writable.
- **Checkpoint H** after Task 35: all indicators implemented + regime classifier + tested; one combined integration test against a raw-candle fixture produces a complete 48-column `features` row with regime labels.
- **Checkpoint I** after Task 45: all 8 detectors green; signal worker iterates the detector registry; three-stage gating (threshold → cooldown → dedup) passes unit + integration tests; outcome worker schedules all 6 horizons (redbeat-backed).
- **Checkpoint J** after Task 52: backfill tooling (REST raw candles, REST microstructure, features backfill) green against testcontainers.
- **Checkpoint K** after Task 55: Grafana additions, full-stack Phase 1 E2E test, exit-criteria runbook. Task 56 is a human-run smoke test.

---

## Execution Model: Docker-Only (unchanged from Phase 0)

All Python commands run inside containers.

| Plan says | Actually run |
|---|---|
| `pytest <args>` | `MSYS_NO_PATHCONV=1 docker compose run --rm test <args>` |
| `alembic <args>` | `MSYS_NO_PATHCONV=1 docker compose run --rm tools alembic <args>` |
| `ruff check <args>` | `MSYS_NO_PATHCONV=1 docker compose run --rm tools ruff check <args>` |
| `python -m trading_sandwich.<x>` | `MSYS_NO_PATHCONV=1 docker compose run --rm tools python -m trading_sandwich.<x>` |

`MSYS_NO_PATHCONV=1` is required on Git Bash for Windows (prevents `/app/...` being munged into `C:/Program Files/Git/app/...`).

The Docker image built in Phase 0 is kept and extended. Dependency changes (Task 1) trigger one rebuild; after that, source edits require no rebuild thanks to the `/app/src` bind-mount + `PYTHONPATH=/app/src` pattern from Phase 0.

---

## File structure (what Phase 1 creates or changes)

**New source packages:**
```
src/trading_sandwich/
├── indicators/
│   ├── __init__.py
│   ├── trend.py           # EMA (new periods), MACD, ADX, DI+/-, RSI (existing), StochRSI, ROC
│   ├── volatility.py      # ATR (existing), BB, Keltner, Donchian
│   ├── volume.py          # OBV, VWAP, volume z-score, MFI
│   ├── structure.py       # swing H/L fractal, pivots, prior-day/week H/L
│   ├── microstructure.py  # funding metrics, OI deltas, L/S ratio, OB imbalance
│   └── regime_inputs.py   # EMA-21 slope bps, ATR percentile, BB-width percentile
├── regime/
│   ├── __init__.py
│   └── classifier.py      # classify() → (trend_regime, vol_regime)
├── signals/
│   ├── detectors/
│   │   ├── __init__.py           # detector registry
│   │   ├── trend_pullback.py     # (exists, Phase 0)
│   │   ├── squeeze_breakout.py
│   │   ├── divergence_rsi.py
│   │   ├── divergence_macd.py
│   │   ├── range_rejection.py
│   │   ├── liquidity_sweep_daily.py
│   │   ├── liquidity_sweep_swing.py
│   │   └── funding_extreme.py
│   └── dedup.py           # dedup gate helper
└── ingestor/
    ├── binance_depth_stream.py
    ├── rest_poller.py
    ├── rest_backfill.py
    └── rest_backfill_microstructure.py
```

**Modified existing modules:** `config.py` (universe expands), `db/models.py` (new ORM classes + extended `Features`), `contracts/models.py` (expanded `Archetype` Literal + extended `FeaturesRow`), `features/compute.py`, `features/worker.py`, `signals/gating.py`, `signals/worker.py`, `outcomes/worker.py`, `celery_app.py` (redbeat + new beat schedule).

**New migrations:** `migrations/versions/0003_archetype_check.py` through `0009_raw_candles_partition.py`.

**New tests:**
- `tests/unit/test_indicator_*.py` — one per indicator family
- `tests/unit/test_regime_classifier.py`
- `tests/unit/test_detector_*.py` — one per new detector (7 new)
- `tests/unit/test_dedup_gate.py`
- `tests/integration/test_depth_ingestor.py`
- `tests/integration/test_rest_poller.py`
- `tests/integration/test_features_full_row.py` — asserts all 48 columns populated
- `tests/integration/test_signal_worker_dedup.py`
- `tests/integration/test_outcome_horizons_all.py`
- `tests/integration/test_rest_backfill.py`
- `tests/integration/test_features_backfill.py`
- `tests/integration/test_end_to_end_phase_1.py` — crafted pattern fires at least one archetype per regime

**Infra files:** `Dockerfile` (TA-Lib), `docker-compose.yml` (pgbouncer, feature-worker replicas, pgbouncer_exporter), `prometheus.yml` (new scrape targets), `policy.yaml` (full universe + regime thresholds + per-symbol funding + dedup window).

---

## Policy.yaml additions

Before any task runs, the agent should understand that `policy.yaml` will end Phase 1 looking like this (used as input by multiple tasks — keep this in mind):

```yaml
trading_enabled: false
execution_mode: paper

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

regime:
  trend_slope_threshold_bps: 2.0
  adx_trend_threshold: 20
  squeeze_percentile: 20
  expansion_percentile: 80

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

gating:
  dedup_window_minutes: 30

outcome_horizons:
  - "15m"
  - "1h"
  - "4h"
  - "24h"
  - "3d"
  - "7d"
```

Tasks that mutate `policy.yaml` commit the diff independently of source changes.

---

## Task 1: Add Phase 1 dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `Dockerfile`

Phase 1 adds `ta-lib` (Python bindings), `celery-redbeat`, and `httpx` (REST polling — ccxt's REST is sync, we want async for Celery tasks).

- [ ] **Step 1: Extend `pyproject.toml` dependencies**

In `pyproject.toml`, under `[project] dependencies`, add:
```toml
  "ta-lib>=0.4.32",
  "celery-redbeat>=2.2",
  "httpx>=0.27",
```

Under `[project.optional-dependencies] dev`, add:
```toml
  "pytest-timeout>=2.3",
```

- [ ] **Step 2: Update Dockerfile to install TA-Lib via pinned Debian package**

Modify the `RUN apt-get update && apt-get install …` block in `Dockerfile` to add `libta-lib0` and `libta-lib0-dev`. Replace the Phase 0 block with:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
        libta-lib0 \
        libta-lib0-dev \
    && rm -rf /var/lib/apt/lists/*
```

**Why this works (Phase 0 learning):** TA-Lib 0.4.0 source build fails on Debian trixie. Debian trixie ships `libta-lib0` 0.6.x as a pinned package. The Python `TA-Lib` wheel links against this system library.

**Failure mode to watch for:** if Debian testing hasn't updated `libta-lib0` yet for the base image's release, pin the image to a specific Debian snapshot. The plan stays resilient either way — the moment `apt-get install libta-lib0` fails with "Unable to locate package", the agent pivots to a specific snapshot tag and updates `FROM python:3.12-slim` accordingly.

- [ ] **Step 3: Extend the uv pip install line in Dockerfile**

In the `RUN --mount=type=cache,target=/root/.cache/uv uv pip install --system ...` block, add the three new packages anywhere in the list:
```
      ta-lib>=0.4.32 \
      celery-redbeat>=2.2 \
      httpx>=0.27 \
      pytest-timeout>=2.3 \
```

- [ ] **Step 4: Rebuild the image once**

Run:
```bash
cd /d/Personal/Projects/trading-mcp-sandwich
DOCKER_BUILDKIT=1 docker compose build tools
```

Expected: successful build in ~2-5 minutes. If the TA-Lib system package isn't available, see Step 2 "Failure mode to watch for".

- [ ] **Step 5: Verify TA-Lib is importable in the image**

Run:
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm tools python -c "import talib; print(talib.__version__); print(talib.EMA([1,2,3,4,5,6,7,8,9,10], timeperiod=3))"
```

Expected: prints the version and a numpy array `[nan nan 2.0 3.0 4.0 5.0 6.0 7.0 8.0 9.0]` (EMA with timeperiod=3 on the sequence).

- [ ] **Step 6: Verify existing suite still passes**

Run:
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/ -q
```

Expected: 27 passed (Phase 0's unit count).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml Dockerfile
git commit -m "chore: add Phase 1 dependencies (ta-lib, celery-redbeat, httpx)"
```

---

## Task 2: Add pgbouncer to `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`
- Create: `pgbouncer/userlist.txt`
- Create: `pgbouncer/pgbouncer.ini`

pgbouncer fronts Postgres for all application services. Alembic and backfill tools continue to connect to `postgres:5432` directly.

- [ ] **Step 1: Create pgbouncer configuration files**

Create `pgbouncer/pgbouncer.ini`:
```ini
[databases]
trading_sandwich = host=postgres port=5432 dbname=trading_sandwich

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = session
max_client_conn = 100
default_pool_size = 20
reserve_pool_size = 5
reserve_pool_timeout = 3
server_reset_query = DISCARD ALL
server_idle_timeout = 600
log_connections = 0
log_disconnections = 0
stats_period = 60
```

**Session-pool mode rationale:** asyncpg prepared statements break in transaction-pool mode (statements are cached server-side but pgbouncer rotates connections). Session mode is safe and gives us connection fan-out across Celery tasks. `max_client_conn=100` covers ingestor (1) + feature-worker ×4 + signal-worker (1) + outcome-worker (1) + beat (1) + cli (1) × async pool headroom.

Create `pgbouncer/userlist.txt`:
```
"trading" "md5REPLACE_ME_BEFORE_DEPLOY"
```

**Generating the md5 hash:** `echo -n "change_me${POSTGRES_USER}" | md5sum | awk '{print "md5" $1}'`. Where `change_me` is the Postgres password from `.env`. This string goes in place of `md5REPLACE_ME_BEFORE_DEPLOY`. The deploy runbook (Task 54) notes this.

For the test environment: `tests/conftest.py` fixtures connect directly to the testcontainer Postgres (no pgbouncer in tests), so the hash in the committed `userlist.txt` is a placeholder. The deploy runbook replaces it.

- [ ] **Step 2: Add pgbouncer + pgbouncer_exporter services to `docker-compose.yml`**

Insert between the existing `postgres:` and `redis:` service definitions:
```yaml
  pgbouncer:
    image: edoburu/pgbouncer:latest
    restart: unless-stopped
    environment:
      DB_USER: ${POSTGRES_USER}
      DB_PASSWORD: ${POSTGRES_PASSWORD}
      DB_NAME: ${POSTGRES_DB}
      DB_HOST: postgres
      DB_PORT: 5432
      POOL_MODE: session
      MAX_CLIENT_CONN: 100
      DEFAULT_POOL_SIZE: 20
    depends_on:
      postgres: {condition: service_healthy}
    ports:
      - "6432:6432"

  pgbouncer-exporter:
    image: prometheuscommunity/pgbouncer-exporter:latest
    restart: unless-stopped
    environment:
      PGBOUNCER_EXPORTER_HOST: 0.0.0.0
      PGBOUNCER_EXPORTER_PORT: 9127
      PGBOUNCER_CONNECTION_STRING: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@pgbouncer:6432/pgbouncer?sslmode=disable
    depends_on:
      - pgbouncer
```

- [ ] **Step 3: Verify compose config parses**

Run:
```bash
cd /d/Personal/Projects/trading-mcp-sandwich
docker compose config --quiet
```

Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add pgbouncer/ docker-compose.yml
git commit -m "chore: add pgbouncer + pgbouncer_exporter to compose"
```

Note: pgbouncer is NOT yet wired as the application services' DB host. That swap happens in Task 10 after all downstream services are confirmed working.

---

## Task 3: Scale feature-worker to 4 replicas

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add `deploy.replicas: 4` to the feature-worker service**

In `docker-compose.yml`, find the `feature-worker:` service block and add:
```yaml
  feature-worker:
    build: .
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
    deploy:
      replicas: 4
    command: ["celery", "-A", "trading_sandwich.celery_app", "worker",
              "-Q", "features", "-n", "features@%h", "--loglevel=info"]
```

**Port collision note:** Phase 0 hardcoded each worker's Prometheus scrape port via `worker_process_init` signal inspecting the hostname prefix (`features` → 9101). With 4 replicas all claiming 9101, only one will succeed and the others will silently skip exporting metrics. Task 42 fixes this properly by using a deterministic port-per-replica scheme; for now, accept that only one of the four feature-workers will serve `/metrics` until Task 42 lands.

- [ ] **Step 2: Verify compose config parses**

Run: `docker compose config --quiet`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: scale feature-worker to 4 replicas"
```

---

## Task 4: Swap to celery-redbeat scheduler

**Files:**
- Modify: `src/trading_sandwich/celery_app.py`
- Modify: `docker-compose.yml`
- Test: `tests/unit/test_celery_app.py`

redbeat stores schedule state in Redis so 3d/7d outcome countdowns survive beat restarts.

- [ ] **Step 1: Extend celery test to assert redbeat scheduler**

Append to `tests/unit/test_celery_app.py`:
```python
def test_redbeat_scheduler_class_set():
    assert app.conf.beat_scheduler == "redbeat.RedBeatScheduler"


def test_redbeat_redis_url_distinct():
    # redbeat keys go into Redis db 2 — distinct from broker (db 0) and backend (db 1)
    assert app.conf.redbeat_redis_url.endswith("/2")
```

- [ ] **Step 2: Run tests to see them fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_celery_app.py -v`
Expected: 2 new FAIL (old 2 pass).

- [ ] **Step 3: Update `src/trading_sandwich/celery_app.py`**

In the `app.conf.update(...)` call, add:
```python
    beat_scheduler="redbeat.RedBeatScheduler",
    redbeat_redis_url=settings.celery_broker_url.rsplit("/", 1)[0] + "/2",
    redbeat_lock_timeout=300,
```

Remove any existing `beat_schedule={}` → replace with the redbeat-compatible pattern (redbeat reads schedule entries at runtime; empty schedule is still valid):
```python
    beat_schedule={},
```

Leave as-is if it was already there.

- [ ] **Step 4: Run tests to verify pass**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_celery_app.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Verify celery-beat service in compose uses the new scheduler**

In `docker-compose.yml`, the `celery-beat:` service command is:
```yaml
    command: ["celery", "-A", "trading_sandwich.celery_app", "beat",
              "--scheduler=redbeat.RedBeatScheduler", "--loglevel=info"]
```

(The `--scheduler` flag is redundant because the app config sets it, but passing it explicitly in the command line is belt-and-braces and makes container inspection obvious.)

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/celery_app.py docker-compose.yml tests/unit/test_celery_app.py
git commit -m "feat: switch Celery Beat to redbeat for persistence"
```

---

## Task 5: Expand `Archetype` Literal in contracts

**Files:**
- Modify: `src/trading_sandwich/contracts/models.py`
- Test: extend `tests/unit/test_contracts.py`

- [ ] **Step 1: Extend `tests/unit/test_contracts.py` with archetype coverage tests**

Append to `tests/unit/test_contracts.py`:
```python
from decimal import Decimal
from uuid import uuid4
from datetime import UTC, datetime

from trading_sandwich.contracts.models import Signal


_PHASE_1_ARCHETYPES = [
    "trend_pullback", "squeeze_breakout",
    "divergence_rsi", "divergence_macd",
    "range_rejection",
    "liquidity_sweep_daily", "liquidity_sweep_swing",
    "funding_extreme",
]


def test_all_phase_1_archetypes_accepted():
    for arch in _PHASE_1_ARCHETYPES:
        s = Signal(
            signal_id=uuid4(), symbol="BTCUSDT", timeframe="5m",
            archetype=arch,
            fired_at=datetime.now(UTC),
            candle_close_time=datetime.now(UTC),
            trigger_price=Decimal("100"), direction="long",
            confidence=Decimal("0.7"),
            confidence_breakdown={},
            gating_outcome="below_threshold",
            features_snapshot={},
            detector_version="test",
        )
        assert s.archetype == arch


def test_unknown_archetype_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Signal(
            signal_id=uuid4(), symbol="BTCUSDT", timeframe="5m",
            archetype="nonexistent_archetype",
            fired_at=datetime.now(UTC),
            candle_close_time=datetime.now(UTC),
            trigger_price=Decimal("100"), direction="long",
            confidence=Decimal("0.7"),
            confidence_breakdown={},
            gating_outcome="below_threshold",
            features_snapshot={},
            detector_version="test",
        )
```

- [ ] **Step 2: Run tests to see them fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_contracts.py::test_all_phase_1_archetypes_accepted -v`
Expected: FAIL — pydantic rejects the new archetype strings.

- [ ] **Step 3: Expand the `Archetype` Literal**

In `src/trading_sandwich/contracts/models.py`, replace:
```python
Archetype = Literal[
    "trend_pullback", "squeeze_breakout", "divergence",
    "liquidity_sweep", "funding_extreme", "range_rejection",
]
```

with:
```python
Archetype = Literal[
    "trend_pullback",
    "squeeze_breakout",
    "divergence_rsi",
    "divergence_macd",
    "range_rejection",
    "liquidity_sweep_daily",
    "liquidity_sweep_swing",
    "funding_extreme",
]
```

- [ ] **Step 4: Run tests to confirm pass**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_contracts.py -v`
Expected: all PASS (6 tests total in this file now).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/contracts/models.py tests/unit/test_contracts.py
git commit -m "feat: expand Archetype Literal to Phase 1 list (8 archetypes)"
```

---

## Task 6: Expand `FeaturesRow` contract with 48 Phase 1 columns

**Files:**
- Modify: `src/trading_sandwich/contracts/models.py`
- Test: extend `tests/unit/test_contracts.py`

- [ ] **Step 1: Append a test that roundtrips a fully-populated FeaturesRow**

Append to `tests/unit/test_contracts.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

from trading_sandwich.contracts.models import FeaturesRow

_PHASE_1_COLUMNS = [
    "ema_8", "ema_21", "ema_55", "ema_200",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "rsi_14", "atr_14",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "swing_high_5", "swing_low_5",
    "pivot_p", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2",
    "prior_day_high", "prior_day_low", "prior_week_high", "prior_week_low",
    "funding_rate", "funding_rate_24h_mean",
    "open_interest_usd", "oi_delta_1h", "oi_delta_24h",
    "long_short_ratio", "ob_imbalance_05",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
]


def test_features_row_accepts_all_phase_1_columns():
    kwargs = {
        "symbol": "BTCUSDT", "timeframe": "5m",
        "close_time": datetime.now(UTC),
        "close_price": Decimal("100"),
        "feature_version": "test",
    }
    for col in _PHASE_1_COLUMNS:
        kwargs[col] = Decimal("1.23") if "flag" not in col else Decimal("0")
    row = FeaturesRow(**kwargs)
    for col in _PHASE_1_COLUMNS:
        assert getattr(row, col) == Decimal("1.23")


def test_features_row_all_new_columns_optional():
    row = FeaturesRow(
        symbol="BTCUSDT", timeframe="5m",
        close_time=datetime.now(UTC),
        close_price=Decimal("100"),
        feature_version="test",
    )
    for col in _PHASE_1_COLUMNS:
        assert getattr(row, col) is None, f"{col} should default to None"
```

- [ ] **Step 2: Run tests to see them fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_contracts.py::test_features_row_accepts_all_phase_1_columns -v`
Expected: FAIL — unknown kwargs (forbid-extra model).

- [ ] **Step 3: Extend `FeaturesRow` with the 48 columns**

In `src/trading_sandwich/contracts/models.py`, find the `class FeaturesRow(_Base):` block. Below the existing `ema_21`, `rsi_14`, `atr_14` fields (keep them), add (before `trend_regime`):

```python
    # Phase 1 extensions — all nullable
    ema_8: Decimal | None = None
    ema_55: Decimal | None = None
    ema_200: Decimal | None = None

    macd_line: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_hist: Decimal | None = None

    adx_14: Decimal | None = None
    di_plus_14: Decimal | None = None
    di_minus_14: Decimal | None = None

    stoch_rsi_k: Decimal | None = None
    stoch_rsi_d: Decimal | None = None
    roc_10: Decimal | None = None

    bb_upper: Decimal | None = None
    bb_middle: Decimal | None = None
    bb_lower: Decimal | None = None
    bb_width: Decimal | None = None

    keltner_upper: Decimal | None = None
    keltner_middle: Decimal | None = None
    keltner_lower: Decimal | None = None

    donchian_upper: Decimal | None = None
    donchian_middle: Decimal | None = None
    donchian_lower: Decimal | None = None

    obv: Decimal | None = None
    vwap: Decimal | None = None
    volume_zscore_20: Decimal | None = None
    mfi_14: Decimal | None = None

    swing_high_5: Decimal | None = None
    swing_low_5: Decimal | None = None

    pivot_p: Decimal | None = None
    pivot_r1: Decimal | None = None
    pivot_r2: Decimal | None = None
    pivot_s1: Decimal | None = None
    pivot_s2: Decimal | None = None

    prior_day_high: Decimal | None = None
    prior_day_low: Decimal | None = None
    prior_week_high: Decimal | None = None
    prior_week_low: Decimal | None = None

    funding_rate: Decimal | None = None
    funding_rate_24h_mean: Decimal | None = None

    open_interest_usd: Decimal | None = None
    oi_delta_1h: Decimal | None = None
    oi_delta_24h: Decimal | None = None

    long_short_ratio: Decimal | None = None
    ob_imbalance_05: Decimal | None = None

    ema_21_slope_bps: Decimal | None = None
    atr_percentile_100: Decimal | None = None
    bb_width_percentile_100: Decimal | None = None
```

- [ ] **Step 4: Run tests to confirm pass**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_contracts.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/contracts/models.py tests/unit/test_contracts.py
git commit -m "feat: extend FeaturesRow with 48 Phase 1 columns (all optional)"
```

---

## Task 7: Alembic migration 0003 — expand features table

**Files:**
- Create: `migrations/versions/0003_features_phase_1_columns.py`
- Test: extend `tests/integration/test_db_migrations.py`

- [ ] **Step 1: Extend the migration test to assert new columns exist**

Append to `tests/integration/test_db_migrations.py`:
```python
_PHASE_1_FEATURES_COLUMNS = [
    "ema_8", "ema_55", "ema_200",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "swing_high_5", "swing_low_5",
    "pivot_p", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2",
    "prior_day_high", "prior_day_low", "prior_week_high", "prior_week_low",
    "funding_rate", "funding_rate_24h_mean",
    "open_interest_usd", "oi_delta_1h", "oi_delta_24h",
    "long_short_ratio", "ob_imbalance_05",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
]


@pytest.mark.integration
def test_features_has_phase_1_columns(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _assert_cols() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    rows = (await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='features'"
                    ))).scalars().all()
                    for col in _PHASE_1_FEATURES_COLUMNS:
                        assert col in rows, f"column {col} missing from features"
            finally:
                await engine.dispose()
        asyncio.run(_assert_cols())
```

- [ ] **Step 2: Run to see failure**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_features_has_phase_1_columns -v -m integration`
Expected: FAIL — columns missing (only `ema_21`, `rsi_14`, `atr_14` from Phase 0).

- [ ] **Step 3: Create migration 0003**

Create `migrations/versions/0003_features_phase_1_columns.py`:
```python
"""features_phase_1_columns

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


_NEW_COLUMNS = [
    "ema_8", "ema_55", "ema_200",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "swing_high_5", "swing_low_5",
    "pivot_p", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2",
    "prior_day_high", "prior_day_low", "prior_week_high", "prior_week_low",
    "funding_rate", "funding_rate_24h_mean",
    "open_interest_usd", "oi_delta_1h", "oi_delta_24h",
    "long_short_ratio", "ob_imbalance_05",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
]


def upgrade() -> None:
    for col in _NEW_COLUMNS:
        op.add_column("features", sa.Column(col, sa.Numeric, nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLUMNS):
        op.drop_column("features", col)
```

- [ ] **Step 4: Run the integration test**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_features_has_phase_1_columns -v -m integration`
Expected: PASS.

- [ ] **Step 5: Also run the whole migrations file to ensure nothing regressed**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py -v -m integration`
Expected: all PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0003_features_phase_1_columns.py tests/integration/test_db_migrations.py
git commit -m "feat: migration 0003 — extend features with Phase 1 columns"
```

---

## Task 8: Extend `Features` ORM model with Phase 1 columns

**Files:**
- Modify: `src/trading_sandwich/db/models.py`

ORM model must mirror the migration so SQLAlchemy queries return the new columns and writes accept them.

- [ ] **Step 1: Add 48 columns to `class Features(Base)`**

In `src/trading_sandwich/db/models.py`, find `class Features(Base):`. Below the existing `ema_21`, `rsi_14`, `atr_14` (keep them), and above `trend_regime`, add:
```python
    ema_8: Mapped[Decimal | None] = mapped_column(Numeric)
    ema_55: Mapped[Decimal | None] = mapped_column(Numeric)
    ema_200: Mapped[Decimal | None] = mapped_column(Numeric)

    macd_line: Mapped[Decimal | None] = mapped_column(Numeric)
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric)
    macd_hist: Mapped[Decimal | None] = mapped_column(Numeric)

    adx_14: Mapped[Decimal | None] = mapped_column(Numeric)
    di_plus_14: Mapped[Decimal | None] = mapped_column(Numeric)
    di_minus_14: Mapped[Decimal | None] = mapped_column(Numeric)

    stoch_rsi_k: Mapped[Decimal | None] = mapped_column(Numeric)
    stoch_rsi_d: Mapped[Decimal | None] = mapped_column(Numeric)
    roc_10: Mapped[Decimal | None] = mapped_column(Numeric)

    bb_upper: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_middle: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_lower: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_width: Mapped[Decimal | None] = mapped_column(Numeric)

    keltner_upper: Mapped[Decimal | None] = mapped_column(Numeric)
    keltner_middle: Mapped[Decimal | None] = mapped_column(Numeric)
    keltner_lower: Mapped[Decimal | None] = mapped_column(Numeric)

    donchian_upper: Mapped[Decimal | None] = mapped_column(Numeric)
    donchian_middle: Mapped[Decimal | None] = mapped_column(Numeric)
    donchian_lower: Mapped[Decimal | None] = mapped_column(Numeric)

    obv: Mapped[Decimal | None] = mapped_column(Numeric)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric)
    volume_zscore_20: Mapped[Decimal | None] = mapped_column(Numeric)
    mfi_14: Mapped[Decimal | None] = mapped_column(Numeric)

    swing_high_5: Mapped[Decimal | None] = mapped_column(Numeric)
    swing_low_5: Mapped[Decimal | None] = mapped_column(Numeric)

    pivot_p: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_r1: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_r2: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_s1: Mapped[Decimal | None] = mapped_column(Numeric)
    pivot_s2: Mapped[Decimal | None] = mapped_column(Numeric)

    prior_day_high: Mapped[Decimal | None] = mapped_column(Numeric)
    prior_day_low: Mapped[Decimal | None] = mapped_column(Numeric)
    prior_week_high: Mapped[Decimal | None] = mapped_column(Numeric)
    prior_week_low: Mapped[Decimal | None] = mapped_column(Numeric)

    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    funding_rate_24h_mean: Mapped[Decimal | None] = mapped_column(Numeric)

    open_interest_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    oi_delta_1h: Mapped[Decimal | None] = mapped_column(Numeric)
    oi_delta_24h: Mapped[Decimal | None] = mapped_column(Numeric)

    long_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    ob_imbalance_05: Mapped[Decimal | None] = mapped_column(Numeric)

    ema_21_slope_bps: Mapped[Decimal | None] = mapped_column(Numeric)
    atr_percentile_100: Mapped[Decimal | None] = mapped_column(Numeric)
    bb_width_percentile_100: Mapped[Decimal | None] = mapped_column(Numeric)
```

- [ ] **Step 2: Sanity-check — unit suite still green**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/ -q`
Expected: all PASS.

- [ ] **Step 3: Sanity-check — migrations still green**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py -v -m integration`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/trading_sandwich/db/models.py
git commit -m "feat: extend Features ORM with 48 Phase 1 columns"
```

---

## Task 9: Migration 0004 — raw_orderbook_snapshots + 0005 raw_funding + 0006 raw_open_interest + 0007 raw_long_short_ratio

**Files:**
- Create: `migrations/versions/0004_raw_orderbook_snapshots.py`
- Create: `migrations/versions/0005_raw_funding.py`
- Create: `migrations/versions/0006_raw_open_interest.py`
- Create: `migrations/versions/0007_raw_long_short_ratio.py`
- Modify: `src/trading_sandwich/db/models.py` — add ORM classes
- Test: extend `tests/integration/test_db_migrations.py`

All four migrations + ORM classes + tests go in one task because they follow the same shape; splitting would be duplicative.

- [ ] **Step 1: Extend migration test to assert all four new raw tables exist**

Append to `tests/integration/test_db_migrations.py`:
```python
@pytest.mark.integration
def test_all_phase_1_raw_tables_exist(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(url, [
            "raw_orderbook_snapshots", "raw_funding",
            "raw_open_interest", "raw_long_short_ratio",
        ])
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_all_phase_1_raw_tables_exist -v -m integration`
Expected: FAIL.

- [ ] **Step 3: Create migration 0004 — `raw_orderbook_snapshots`**

Create `migrations/versions/0004_raw_orderbook_snapshots.py`:
```python
"""raw_orderbook_snapshots

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_orderbook_snapshots",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("bids", postgresql.JSONB, nullable=False),
        sa.Column("asks", postgresql.JSONB, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "captured_at"),
    )
    op.create_index(
        "ix_ob_snapshots_symbol_captured_desc",
        "raw_orderbook_snapshots",
        ["symbol", sa.text("captured_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_ob_snapshots_symbol_captured_desc", table_name="raw_orderbook_snapshots")
    op.drop_table("raw_orderbook_snapshots")
```

- [ ] **Step 4: Create migration 0005 — `raw_funding`**

Create `migrations/versions/0005_raw_funding.py`:
```python
"""raw_funding

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_funding",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("settlement_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rate", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "settlement_time"),
    )


def downgrade() -> None:
    op.drop_table("raw_funding")
```

- [ ] **Step 5: Create migration 0006 — `raw_open_interest`**

Create `migrations/versions/0006_raw_open_interest.py`:
```python
"""raw_open_interest

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_open_interest",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open_interest_usd", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "captured_at"),
    )
    op.create_index(
        "ix_oi_symbol_captured_desc",
        "raw_open_interest",
        ["symbol", sa.text("captured_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_oi_symbol_captured_desc", table_name="raw_open_interest")
    op.drop_table("raw_open_interest")
```

- [ ] **Step 6: Create migration 0007 — `raw_long_short_ratio`**

Create `migrations/versions/0007_raw_long_short_ratio.py`:
```python
"""raw_long_short_ratio

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_long_short_ratio",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ratio", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "captured_at"),
    )


def downgrade() -> None:
    op.drop_table("raw_long_short_ratio")
```

- [ ] **Step 7: Add ORM classes to `db/models.py`**

In `src/trading_sandwich/db/models.py`, append after `class ClaudeDecision`:
```python
class RawOrderbookSnapshot(Base):
    __tablename__ = "raw_orderbook_snapshots"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    bids: Mapped[list] = mapped_column(JSONB, nullable=False)
    asks: Mapped[list] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class RawFunding(Base):
    __tablename__ = "raw_funding"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    settlement_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class RawOpenInterest(Base):
    __tablename__ = "raw_open_interest"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    open_interest_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )


class RawLongShortRatio(Base):
    __tablename__ = "raw_long_short_ratio"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    ratio: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
```

- [ ] **Step 8: Run the migration test**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py -v -m integration`
Expected: all PASS (now 5 tests total in that file).

- [ ] **Step 9: Commit**

```bash
git add migrations/versions/0004_raw_orderbook_snapshots.py migrations/versions/0005_raw_funding.py migrations/versions/0006_raw_open_interest.py migrations/versions/0007_raw_long_short_ratio.py src/trading_sandwich/db/models.py tests/integration/test_db_migrations.py
git commit -m "feat: migrations 0004-0007 + ORM — raw orderbook, funding, OI, L/S ratio"
```

---

## Task 10: Update Settings + policy.yaml + compose wiring to pgbouncer

**Files:**
- Modify: `policy.yaml`
- Modify: `src/trading_sandwich/config.py` — new `pgbouncer_host`, `pgbouncer_port` with sensible defaults
- Modify: `docker-compose.yml` — application services point at pgbouncer for DB
- Modify: `.env.example`
- Test: extend `tests/unit/test_config.py`

This task expands `policy.yaml` to the full Phase 1 shape (universe + regime thresholds + per-symbol funding + dedup window + all 6 horizons) and wires the runtime services through pgbouncer. Alembic continues to hit `postgres:5432` directly via its own env-var logic.

- [ ] **Step 1: Expand `policy.yaml` to the Phase 1 shape**

Replace `policy.yaml` with the full content listed in the "Policy.yaml additions" section of this plan (top of file).

- [ ] **Step 2: Add pgbouncer host/port to `.env.example` and `Settings`**

In `.env.example`, add after the Postgres block:
```
# --- pgbouncer (application services connect here instead of POSTGRES_HOST) ---
PGBOUNCER_HOST=pgbouncer
PGBOUNCER_PORT=6432
```

In `src/trading_sandwich/config.py`, add to `Settings`:
```python
    pgbouncer_host: str = "pgbouncer"
    pgbouncer_port: int = 6432
```

And add a new property:
```python
    @property
    def pgbouncer_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.pgbouncer_host}:{self.pgbouncer_port}/{self.postgres_db}"
        )
```

- [ ] **Step 3: Add unit test for the new property**

Append to `tests/unit/test_config.py`:
```python
def test_pgbouncer_url_composition(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "trading")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_DB", "ts")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("PGBOUNCER_HOST", "pgb")
    monkeypatch.setenv("PGBOUNCER_PORT", "7777")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://r/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://r/1")

    import trading_sandwich.config as cfg
    cfg._settings = None
    s = cfg.Settings()
    assert s.pgbouncer_url == "postgresql+asyncpg://trading:secret@pgb:7777/ts"
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire runtime services to pgbouncer URL**

In `src/trading_sandwich/db/engine.py`, change the engine factory to use `pgbouncer_url`:
```python
def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().pgbouncer_url, poolclass=NullPool)
    return _engine
```

Alembic (via `migrations/env.py`) still uses `get_settings().database_url` (which points at `postgres:5432`), so DDL continues to bypass pgbouncer.

- [ ] **Step 6: Verify full test suite still passes**

Run:
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm test -q
```

Expected: all green. Tests still talk to Postgres directly because the `env_for_postgres` fixture in `conftest.py` sets `POSTGRES_HOST` to the testcontainer's address, and the runtime engine calls `get_settings().pgbouncer_url` — but in tests we need the engine to point at the testcontainer, not a non-existent `pgbouncer` host.

**Failure mode:** if tests break because the engine tries to reach `pgbouncer:6432`, update `env_for_postgres` in `tests/conftest.py` to also set `PGBOUNCER_HOST=<host>` and `PGBOUNCER_PORT=<port>` from the testcontainer URL. This is the correct fix — the fixture should reflect the real environment exactly.

Apply this fix in `tests/conftest.py`'s `_apply` function inside `env_for_postgres`:
```python
        monkeypatch.setenv("PGBOUNCER_HOST", host)
        monkeypatch.setenv("PGBOUNCER_PORT", port)
```

Re-run the suite; expected: all green.

- [ ] **Step 7: Commit**

```bash
git add policy.yaml src/trading_sandwich/config.py src/trading_sandwich/db/engine.py .env.example tests/unit/test_config.py tests/conftest.py
git commit -m "feat: wire application engine to pgbouncer; Alembic keeps direct Postgres"
```

---

# Checkpoint F — pause for human review

Tasks 1–10 complete. Infra + contract extensions applied. Schema migrations 0003–0007 applied. Full test suite green against the new configuration. Nothing functional yet — no new indicators, no new detectors.

**Before continuing to Checkpoint G, verify manually:**
```bash
docker compose config --quiet
MSYS_NO_PATHCONV=1 docker compose run --rm tools ruff check src tests
MSYS_NO_PATHCONV=1 docker compose run --rm test -q
```

All three should be green.

---

## Task 11: Migration 0008 — archetype check constraint

**Files:**
- Create: `migrations/versions/0008_archetype_check.py`
- Test: extend `tests/integration/test_db_migrations.py`

A CHECK constraint at the DB level is belt-and-braces against unknown archetype strings sneaking in via the worker's raw INSERT path. Pydantic already rejects at the contract layer; this defends against worker bugs.

- [ ] **Step 1: Extend test to assert constraint rejects a bad archetype**

Append to `tests/integration/test_db_migrations.py`:
```python
@pytest.mark.integration
def test_archetype_check_constraint_rejects_unknown(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _probe() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.begin() as conn:
                    # Good row should insert
                    await conn.execute(text(
                        "INSERT INTO signals (signal_id,symbol,timeframe,archetype,"
                        "fired_at,candle_close_time,trigger_price,direction,confidence,"
                        "confidence_breakdown,gating_outcome,features_snapshot,"
                        "detector_version) VALUES (gen_random_uuid(),'BTCUSDT','5m',"
                        "'trend_pullback',now(),now(),100,'long',0.7,"
                        "CAST('{}' AS jsonb),'below_threshold',"
                        "CAST('{}' AS jsonb),'test')"
                    ))

                # Bad row should be rejected
                import asyncpg.exceptions
                from sqlalchemy.exc import IntegrityError
                with pytest.raises((IntegrityError, asyncpg.exceptions.CheckViolationError)):
                    async with engine.begin() as conn:
                        await conn.execute(text(
                            "INSERT INTO signals (signal_id,symbol,timeframe,archetype,"
                            "fired_at,candle_close_time,trigger_price,direction,confidence,"
                            "confidence_breakdown,gating_outcome,features_snapshot,"
                            "detector_version) VALUES (gen_random_uuid(),'BTCUSDT','5m',"
                            "'nonexistent',now(),now(),100,'long',0.7,"
                            "CAST('{}' AS jsonb),'below_threshold',"
                            "CAST('{}' AS jsonb),'test')"
                        ))
            finally:
                await engine.dispose()
        asyncio.run(_probe())
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_archetype_check_constraint_rejects_unknown -v -m integration`
Expected: FAIL — both INSERTs succeed because no constraint exists.

- [ ] **Step 3: Create migration 0008**

Create `migrations/versions/0008_archetype_check.py`:
```python
"""archetype_check

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


_ARCHETYPES = [
    "trend_pullback",
    "squeeze_breakout",
    "divergence_rsi",
    "divergence_macd",
    "range_rejection",
    "liquidity_sweep_daily",
    "liquidity_sweep_swing",
    "funding_extreme",
]


def upgrade() -> None:
    values = ", ".join(f"'{a}'" for a in _ARCHETYPES)
    op.create_check_constraint(
        "ck_signals_archetype_valid",
        "signals",
        f"archetype IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_signals_archetype_valid", "signals", type_="check")
```

- [ ] **Step 4: Run the test**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_archetype_check_constraint_rejects_unknown -v -m integration`
Expected: PASS.

- [ ] **Step 5: Run all migration tests to catch regressions**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py -v -m integration`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0008_archetype_check.py tests/integration/test_db_migrations.py
git commit -m "feat: migration 0008 — archetype CHECK constraint on signals"
```

---

## Task 12: Migration 0009 — declaratively partition raw_candles by month

**Files:**
- Create: `migrations/versions/0009_raw_candles_partition.py`
- Test: extend `tests/integration/test_db_migrations.py`

Converting the existing `raw_candles` table into a partitioned parent is a non-trivial migration: you cannot ALTER a normal table into a partitioned one in place. The pattern is: create new partitioned table, copy data, rename old out, rename new in, drop old.

For Phase 1's initial deploy the table will have at most 1 year of Phase 0 + backfill data, so the copy is fast (seconds to a minute).

- [ ] **Step 1: Extend migration test to assert raw_candles is partitioned**

Append to `tests/integration/test_db_migrations.py`:
```python
@pytest.mark.integration
def test_raw_candles_is_partitioned(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        async def _probe() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    # pg_partitioned_table lists the parents
                    row = (await conn.execute(text(
                        "SELECT partstrat FROM pg_partitioned_table "
                        "JOIN pg_class ON pg_partitioned_table.partrelid = pg_class.oid "
                        "WHERE pg_class.relname = 'raw_candles'"
                    ))).scalar_one_or_none()
                    assert row == "r", "raw_candles should be RANGE-partitioned"

                    # At least one child partition exists (current month)
                    child_count = (await conn.execute(text(
                        "SELECT count(*) FROM pg_inherits "
                        "JOIN pg_class parent ON pg_inherits.inhparent = parent.oid "
                        "WHERE parent.relname = 'raw_candles'"
                    ))).scalar()
                    assert child_count >= 1, f"expected >=1 partition, got {child_count}"
            finally:
                await engine.dispose()
        asyncio.run(_probe())
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_raw_candles_is_partitioned -v -m integration`
Expected: FAIL — `partstrat` row is None.

- [ ] **Step 3: Create migration 0009**

Create `migrations/versions/0009_raw_candles_partition.py`:
```python
"""raw_candles_partition

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from datetime import UTC, datetime

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()


def upgrade() -> None:
    # 1) Create the partitioned replacement table (same schema, open_time is the
    #    partition key — must be part of the primary key for declarative partitioning).
    op.execute("""
        CREATE TABLE raw_candles_partitioned (
            symbol              TEXT                     NOT NULL,
            timeframe           TEXT                     NOT NULL,
            open_time           TIMESTAMP WITH TIME ZONE NOT NULL,
            close_time          TIMESTAMP WITH TIME ZONE NOT NULL,
            open                NUMERIC                  NOT NULL,
            high                NUMERIC                  NOT NULL,
            low                 NUMERIC                  NOT NULL,
            close               NUMERIC                  NOT NULL,
            volume              NUMERIC                  NOT NULL,
            quote_volume        NUMERIC,
            trade_count         INTEGER,
            taker_buy_base      NUMERIC,
            taker_buy_quote     NUMERIC,
            ingested_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, timeframe, open_time)
        ) PARTITION BY RANGE (open_time);
    """)

    # 2) Create partitions for the 13 months centred on deploy time (6 past + current + 6 future)
    now = datetime.now(UTC)
    year, month = now.year, now.month
    for offset in range(-6, 7):
        m = month + offset
        y = year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        start, end = _month_bounds(y, m)
        op.execute(
            f"CREATE TABLE raw_candles_{y:04d}_{m:02d} "
            f"PARTITION OF raw_candles_partitioned "
            f"FOR VALUES FROM ('{start}') TO ('{end}');"
        )

    # 3) Copy existing data. Phase 0 + backfill data for 8 symbols × 5 TFs × 1 year
    #    is ~1M rows total, INSERT SELECT is seconds.
    op.execute("INSERT INTO raw_candles_partitioned SELECT * FROM raw_candles;")

    # 4) Recreate the supporting index on the new parent (children inherit it)
    op.execute("""
        CREATE INDEX ix_raw_candles_symbol_tf_close_new
        ON raw_candles_partitioned (symbol, timeframe, close_time);
    """)

    # 5) Swap tables
    op.execute("ALTER TABLE raw_candles RENAME TO raw_candles_old;")
    op.execute("ALTER TABLE raw_candles_partitioned RENAME TO raw_candles;")
    op.execute("DROP TABLE raw_candles_old;")
    op.execute("ALTER INDEX ix_raw_candles_symbol_tf_close_new RENAME TO ix_raw_candles_symbol_tf_close;")


def downgrade() -> None:
    # Best-effort reverse: copy all partitioned data into a plain table, swap, drop partitions.
    op.execute("""
        CREATE TABLE raw_candles_unpartitioned (
            symbol              TEXT                     NOT NULL,
            timeframe           TEXT                     NOT NULL,
            open_time           TIMESTAMP WITH TIME ZONE NOT NULL,
            close_time          TIMESTAMP WITH TIME ZONE NOT NULL,
            open                NUMERIC                  NOT NULL,
            high                NUMERIC                  NOT NULL,
            low                 NUMERIC                  NOT NULL,
            close               NUMERIC                  NOT NULL,
            volume              NUMERIC                  NOT NULL,
            quote_volume        NUMERIC,
            trade_count         INTEGER,
            taker_buy_base      NUMERIC,
            taker_buy_quote     NUMERIC,
            ingested_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, timeframe, open_time)
        );
    """)
    op.execute("INSERT INTO raw_candles_unpartitioned SELECT * FROM raw_candles;")
    op.execute("DROP TABLE raw_candles;")
    op.execute("ALTER TABLE raw_candles_unpartitioned RENAME TO raw_candles;")
    op.execute(
        "CREATE INDEX ix_raw_candles_symbol_tf_close "
        "ON raw_candles (symbol, timeframe, close_time);"
    )
```

- [ ] **Step 4: Run the partition test**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py::test_raw_candles_is_partitioned -v -m integration`
Expected: PASS.

- [ ] **Step 5: Run all migration tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_db_migrations.py -v -m integration`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0009_raw_candles_partition.py tests/integration/test_db_migrations.py
git commit -m "feat: migration 0009 — raw_candles partitioned by month on open_time"
```

---

## Task 13: `policy.yaml` loader helper

**Files:**
- Create: `src/trading_sandwich/_policy.py`
- Test: `tests/unit/test_policy_loader.py`

Existing workers load `policy.yaml` ad-hoc. Phase 1 needs many more consumers (regime classifier, dedup gate, 7 new detectors, CLI). Centralize the loader.

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_policy_loader.py`:
```python
from decimal import Decimal

from trading_sandwich._policy import (
    get_cooldown_minutes,
    get_confidence_threshold,
    get_dedup_window_minutes,
    get_funding_threshold,
    get_regime_thresholds,
    load_policy,
)


def test_load_policy_returns_dict():
    p = load_policy()
    assert isinstance(p, dict)
    assert p["trading_enabled"] is False


def test_get_confidence_threshold():
    assert get_confidence_threshold("trend_pullback") == Decimal("0.70")
    assert get_confidence_threshold("divergence_rsi") == Decimal("0.65")


def test_get_cooldown_minutes():
    assert get_cooldown_minutes("trend_pullback") == 30
    assert get_cooldown_minutes("funding_extreme") == 120


def test_get_dedup_window_minutes():
    assert get_dedup_window_minutes() == 30


def test_get_regime_thresholds():
    r = get_regime_thresholds()
    assert r["trend_slope_threshold_bps"] == 2.0
    assert r["adx_trend_threshold"] == 20
    assert r["squeeze_percentile"] == 20
    assert r["expansion_percentile"] == 80


def test_get_funding_threshold_known_symbol():
    long, short = get_funding_threshold("BTCUSDT")
    assert long == Decimal("-0.0003")
    assert short == Decimal("0.0003")


def test_get_funding_threshold_unknown_falls_back_to_default():
    long, short = get_funding_threshold("NOTINUNIVERSE")
    assert long == Decimal("-0.0005")
    assert short == Decimal("0.0005")
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_policy_loader.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement loader**

Create `src/trading_sandwich/_policy.py`:
```python
"""Central `policy.yaml` accessor. Consumers should use these helpers rather than
reading the YAML directly; this gives us one place to change caching or schema
validation when the policy grows.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

_POLICY_PATH = Path("policy.yaml")


@lru_cache(maxsize=1)
def load_policy() -> dict:
    with open(_POLICY_PATH) as f:
        return yaml.safe_load(f)


def get_confidence_threshold(archetype: str) -> Decimal:
    return Decimal(str(load_policy()["per_archetype_confidence_threshold"][archetype]))


def get_cooldown_minutes(archetype: str) -> int:
    return int(load_policy()["per_archetype_cooldown_minutes"][archetype])


def get_dedup_window_minutes() -> int:
    return int(load_policy()["gating"]["dedup_window_minutes"])


def get_regime_thresholds() -> dict:
    return dict(load_policy()["regime"])


def get_funding_threshold(symbol: str) -> tuple[Decimal, Decimal]:
    table = load_policy()["per_symbol_funding_threshold"]
    entry = table.get(symbol, table["default"])
    return Decimal(str(entry["long"])), Decimal(str(entry["short"]))


def reset_cache() -> None:
    """Test hook — policy.yaml changes mid-process (e.g. in a test) need cache bust."""
    load_policy.cache_clear()
```

- [ ] **Step 4: Verify tests pass**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_policy_loader.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/_policy.py tests/unit/test_policy_loader.py
git commit -m "feat: add centralized policy.yaml loader"
```

---

## Task 14: Universe / timeframe helpers

**Files:**
- Create: `src/trading_sandwich/_universe.py`
- Test: `tests/unit/test_universe.py`

`policy.yaml` is now the canonical source for universe and timeframes. Settings' `universe_symbols` / `universe_timeframes` become fallback env-var overrides for tests; production reads from policy.

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_universe.py`:
```python
from trading_sandwich._universe import symbols, timeframes


def test_symbols_from_policy():
    s = symbols()
    assert "BTCUSDT" in s
    assert "ETHUSDT" in s
    assert "SOLUSDT" in s
    assert len(s) == 8


def test_timeframes_from_policy():
    tfs = timeframes()
    assert tfs == ["5m", "15m", "1h", "4h", "1d"]
    assert "1m" not in tfs
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_universe.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/_universe.py`:
```python
"""Universe + timeframes — sourced from policy.yaml (canonical). Tests that
need a different universe monkeypatch policy.yaml or the env vars; production
always reads policy.yaml.
"""
from __future__ import annotations

from trading_sandwich._policy import load_policy


def symbols() -> list[str]:
    return list(load_policy()["universe"])


def timeframes() -> list[str]:
    return list(load_policy()["timeframes"])
```

- [ ] **Step 4: Verify tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_universe.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/_universe.py tests/unit/test_universe.py
git commit -m "feat: add universe/timeframes helpers sourcing from policy.yaml"
```

---

## Task 15: Indicator package scaffold

**Files:**
- Create: `src/trading_sandwich/indicators/__init__.py`
- Create: `tests/unit/_indicator_fixtures.py`

All indicator modules share a deterministic candle fixture. Define it once.

- [ ] **Step 1: Create empty package**

Create `src/trading_sandwich/indicators/__init__.py`:
```python
"""Indicator functions, one module per family. Input: pandas Series/DataFrame.
Output: same-length Series or tuple of Series. All NaN-padded at warmup.
"""
```

- [ ] **Step 2: Create fixture module for tests**

Create `tests/unit/_indicator_fixtures.py`:
```python
"""Shared deterministic candle DataFrames for indicator tests."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_btc_1m_synthetic() -> pd.DataFrame:
    """30 BTC 1m candles crafted in Phase 0. Good enough for most warmup tests."""
    data = json.loads(Path("tests/fixtures/candles_btc_1m_synthetic.json").read_text())
    df = pd.DataFrame(
        data["candles"],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def linear_uptrend(n: int = 300) -> pd.DataFrame:
    """n 1m candles rising linearly by 0.5 per bar, high/low = close ± 0.3,
    volume = 10. Useful for trend indicator tests that need ≥200 bars (EMA-200).
    """
    rows = []
    for i in range(n):
        c = 100.0 + i * 0.5
        rows.append({
            "ts": 1700000000000 + i * 60_000,
            "open": c - 0.1, "high": c + 0.3, "low": c - 0.3,
            "close": c, "volume": 10.0,
        })
    df = pd.DataFrame(rows)
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def noisy_flat(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """n 1m candles oscillating around 100 with low variance. Useful for
    range/squeeze regime tests.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    closes = 100.0 + rng.standard_normal(n) * 0.5
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "ts": 1700000000000 + i * 60_000,
            "open": c - 0.05, "high": c + 0.2, "low": c - 0.2,
            "close": float(c), "volume": 10.0,
        })
    df = pd.DataFrame(rows)
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df
```

- [ ] **Step 3: Sanity check — importable**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm tools python -c "from tests.unit._indicator_fixtures import linear_uptrend; print(len(linear_uptrend()))"`
Expected: prints `300`.

- [ ] **Step 4: Commit**

```bash
git add src/trading_sandwich/indicators/__init__.py tests/unit/_indicator_fixtures.py
git commit -m "feat: scaffold indicators package + shared test fixtures"
```

---

## Task 16: `indicators/trend.py` — EMA (4 periods), MACD, ADX, StochRSI, ROC

**Files:**
- Create: `src/trading_sandwich/indicators/trend.py`
- Test: `tests/unit/test_indicator_trend.py`

RSI already exists in Phase 0's `features/compute.py`. Phase 1 moves it here and adds the rest. The Phase 0 `features/compute.py` keeps working because Task 28 rewrites it as an orchestrator over the new indicator modules.

- [ ] **Step 1: Write failing tests — one per indicator in the family**

Create `tests/unit/test_indicator_trend.py`:
```python
from tests.unit._indicator_fixtures import linear_uptrend, load_btc_1m_synthetic
from trading_sandwich.indicators.trend import (
    compute_adx,
    compute_ema,
    compute_macd,
    compute_roc,
    compute_rsi,
    compute_stoch_rsi,
)


def test_ema_matches_period_sma_at_warmup():
    df = load_btc_1m_synthetic()
    ema = compute_ema(df["close"], period=21)
    assert ema.iloc[:20].isna().all()
    # TA-Lib seeds EMA with SMA of first `period` values
    sma21 = df["close"].iloc[:21].mean()
    assert abs(float(ema.iloc[20]) - sma21) < 0.01


def test_ema_length_matches_input():
    df = linear_uptrend(n=250)
    ema = compute_ema(df["close"], period=200)
    assert len(ema) == 250


def test_macd_returns_three_series_same_length():
    df = linear_uptrend(n=300)
    line, signal, hist = compute_macd(df["close"])
    assert len(line) == len(signal) == len(hist) == 300
    # Line has valid values from bar 25 onward (slow EMA-26 warmup)
    assert line.iloc[:25].isna().all()
    assert line.iloc[30:].notna().all()


def test_adx_positive_in_trend():
    df = linear_uptrend(n=100)
    adx, di_plus, di_minus = compute_adx(df["high"], df["low"], df["close"], period=14)
    valid = adx.dropna()
    # In a clean linear uptrend, ADX stabilises >25 and DI+ > DI-
    assert (valid.iloc[-10:] > 25).all()
    assert (di_plus.iloc[-10:] > di_minus.iloc[-10:]).all()


def test_rsi_bounds():
    df = load_btc_1m_synthetic()
    rsi = compute_rsi(df["close"], period=14)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_stoch_rsi_bounds():
    df = linear_uptrend(n=100)
    k, d = compute_stoch_rsi(df["close"], rsi_period=14, stoch_period=14, k=3, d=3)
    for series in (k, d):
        valid = series.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()


def test_roc_on_linear_uptrend():
    df = linear_uptrend(n=100)
    roc = compute_roc(df["close"], period=10)
    # Close rises by 5 every 10 bars (0.5/bar × 10) from a base ~100+
    valid = roc.dropna()
    assert (valid > 0).all()
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_trend.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `indicators/trend.py`**

Create `src/trading_sandwich/indicators/trend.py`:
```python
"""Trend + momentum indicators. TA-Lib backed where available; pandas-ta
fallbacks otherwise.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import talib


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    out = talib.EMA(close.to_numpy(dtype=float), timeperiod=period)
    return pd.Series(out, index=close.index, name=f"ema_{period}")


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    out = talib.RSI(close.to_numpy(dtype=float), timeperiod=period)
    return pd.Series(out, index=close.index, name=f"rsi_{period}")


def compute_macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    line, sig, hist = talib.MACD(
        close.to_numpy(dtype=float),
        fastperiod=fast, slowperiod=slow, signalperiod=signal,
    )
    return (
        pd.Series(line, index=close.index, name="macd_line"),
        pd.Series(sig, index=close.index, name="macd_signal"),
        pd.Series(hist, index=close.index, name="macd_hist"),
    )


def compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    adx = talib.ADX(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    di_plus = talib.PLUS_DI(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    di_minus = talib.MINUS_DI(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    return (
        pd.Series(adx, index=high.index, name=f"adx_{period}"),
        pd.Series(di_plus, index=high.index, name=f"di_plus_{period}"),
        pd.Series(di_minus, index=high.index, name=f"di_minus_{period}"),
    )


def compute_stoch_rsi(
    close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
    k: int = 3, d: int = 3,
) -> tuple[pd.Series, pd.Series]:
    k_vals, d_vals = talib.STOCHRSI(
        close.to_numpy(dtype=float),
        timeperiod=rsi_period, fastk_period=stoch_period,
        fastd_period=d, fastd_matype=0,
    )
    # TA-Lib's STOCHRSI emits a fast%K + fast%D; treat fast%K as smoothed by `k`
    # post-hoc to match convention. For Phase 0 purposes TA-Lib's output is fine.
    return (
        pd.Series(k_vals, index=close.index, name="stoch_rsi_k"),
        pd.Series(d_vals, index=close.index, name="stoch_rsi_d"),
    )


def compute_roc(close: pd.Series, period: int = 10) -> pd.Series:
    out = talib.ROC(close.to_numpy(dtype=float), timeperiod=period)
    return pd.Series(out, index=close.index, name=f"roc_{period}")
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_trend.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/indicators/trend.py tests/unit/test_indicator_trend.py
git commit -m "feat: add trend/momentum indicators (EMA, RSI, MACD, ADX, StochRSI, ROC)"
```

---

## Task 17: `indicators/volatility.py` — ATR, Bollinger, Keltner, Donchian

**Files:**
- Create: `src/trading_sandwich/indicators/volatility.py`
- Test: `tests/unit/test_indicator_volatility.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_indicator_volatility.py`:
```python
from tests.unit._indicator_fixtures import linear_uptrend, load_btc_1m_synthetic
from trading_sandwich.indicators.volatility import (
    compute_atr,
    compute_bollinger,
    compute_donchian,
    compute_keltner,
)


def test_atr_positive_for_real_data():
    df = load_btc_1m_synthetic()
    atr = compute_atr(df["high"], df["low"], df["close"], period=14)
    valid = atr.dropna()
    assert (valid > 0).all()


def test_bollinger_upper_above_lower():
    df = linear_uptrend(n=50)
    upper, middle, lower, width = compute_bollinger(df["close"], period=20, std=2)
    mask = upper.notna()
    assert (upper[mask] >= middle[mask]).all()
    assert (middle[mask] >= lower[mask]).all()
    assert (width[mask] >= 0).all()


def test_bollinger_width_near_zero_in_flat():
    from tests.unit._indicator_fixtures import noisy_flat
    df = noisy_flat(n=300)
    _, _, _, width = compute_bollinger(df["close"], period=20, std=2)
    valid = width.dropna()
    # Width in flat noise is small compared to price; test just that it's bounded
    assert valid.iloc[-50:].max() < 10.0


def test_keltner_middle_is_ema():
    df = linear_uptrend(n=50)
    upper, middle, lower = compute_keltner(df["high"], df["low"], df["close"], period=20, atr_mult=2)
    # Middle is EMA-20 — should be ≤ close in a clean uptrend
    mask = middle.notna()
    assert (middle[mask] <= df["close"][mask]).all()
    assert (upper[mask] > middle[mask]).all()
    assert (lower[mask] < middle[mask]).all()


def test_donchian_upper_is_rolling_max():
    df = linear_uptrend(n=50)
    upper, middle, lower = compute_donchian(df["high"], df["low"], period=20)
    # Expected: upper at index 30 = max(high over bars 11..30)
    expected_upper_30 = df["high"].iloc[11:31].max()
    assert abs(float(upper.iloc[30]) - expected_upper_30) < 1e-6
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_volatility.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/indicators/volatility.py`:
```python
"""Volatility + range indicators."""
from __future__ import annotations

import pandas as pd
import talib


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
) -> pd.Series:
    out = talib.ATR(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    return pd.Series(out, index=high.index, name=f"atr_{period}")


def compute_bollinger(
    close: pd.Series, period: int = 20, std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    upper, middle, lower = talib.BBANDS(
        close.to_numpy(dtype=float),
        timeperiod=period, nbdevup=std, nbdevdn=std, matype=0,
    )
    width = (upper - lower)  # absolute width in price units
    return (
        pd.Series(upper, index=close.index, name="bb_upper"),
        pd.Series(middle, index=close.index, name="bb_middle"),
        pd.Series(lower, index=close.index, name="bb_lower"),
        pd.Series(width, index=close.index, name="bb_width"),
    )


def compute_keltner(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 20, atr_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = talib.EMA(close.to_numpy(dtype=float), timeperiod=period)
    atr = talib.ATR(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float), close.to_numpy(dtype=float),
        timeperiod=period,
    )
    upper = middle + atr * atr_mult
    lower = middle - atr * atr_mult
    return (
        pd.Series(upper, index=close.index, name="keltner_upper"),
        pd.Series(middle, index=close.index, name="keltner_middle"),
        pd.Series(lower, index=close.index, name="keltner_lower"),
    )


def compute_donchian(
    high: pd.Series, low: pd.Series, period: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    upper = high.rolling(window=period).max()
    lower = low.rolling(window=period).min()
    middle = (upper + lower) / 2.0
    return (
        upper.rename("donchian_upper"),
        middle.rename("donchian_middle"),
        lower.rename("donchian_lower"),
    )
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_volatility.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/indicators/volatility.py tests/unit/test_indicator_volatility.py
git commit -m "feat: add volatility indicators (ATR, Bollinger, Keltner, Donchian)"
```

---

## Task 18: `indicators/volume.py` — OBV, VWAP, volume z-score, MFI

**Files:**
- Create: `src/trading_sandwich/indicators/volume.py`
- Test: `tests/unit/test_indicator_volume.py`

VWAP is session-anchored (daily reset at 00:00 UTC). That means VWAP needs each candle's `close_time` to know which session it belongs to.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_indicator_volume.py`:
```python
from datetime import UTC, datetime, timedelta

import pandas as pd

from trading_sandwich.indicators.volume import (
    compute_mfi,
    compute_obv,
    compute_volume_zscore,
    compute_vwap_session,
)


def _build(n: int = 50, start_hour: int = 10) -> pd.DataFrame:
    base = datetime(2026, 4, 21, start_hour, 0, tzinfo=UTC)
    rows = []
    for i in range(n):
        close = 100.0 + i * 0.2
        rows.append({
            "close_time": base + timedelta(minutes=i),
            "open": close - 0.1, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": 10 + i,
        })
    return pd.DataFrame(rows)


def test_obv_monotonic_in_uptrend():
    df = _build()
    obv = compute_obv(df["close"], df["volume"])
    # In a monotonic uptrend all volume is positive, OBV strictly increases
    assert (obv.diff().dropna() > 0).all()


def test_vwap_resets_at_midnight_utc():
    # 60 candles crossing midnight
    base = datetime(2026, 4, 21, 23, 30, tzinfo=UTC)
    rows = []
    for i in range(60):
        close = 100.0 + i * 0.1
        rows.append({
            "close_time": base + timedelta(minutes=i),
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": 10.0,
        })
    df = pd.DataFrame(rows)
    vwap = compute_vwap_session(df)
    assert not vwap.isna().all()
    # At the first post-midnight bar (i=30 = 00:00), VWAP equals that bar's
    # typical price — session was reset.
    assert abs(float(vwap.iloc[30]) - float(df["close"].iloc[30])) < 0.1


def test_volume_zscore_mean_zero_over_window():
    df = _build(n=200)
    z = compute_volume_zscore(df["volume"], window=20)
    valid = z.dropna()
    # z-score distribution should be roughly centred; not strict but sanity
    assert abs(float(valid.mean())) < 1.0


def test_mfi_bounds():
    df = _build(n=50)
    mfi = compute_mfi(df["high"], df["low"], df["close"], df["volume"], period=14)
    valid = mfi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_volume.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/indicators/volume.py`:
```python
"""Volume + flow indicators. VWAP is session-anchored (00:00 UTC daily reset)."""
from __future__ import annotations

import pandas as pd
import talib


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    out = talib.OBV(close.to_numpy(dtype=float), volume.to_numpy(dtype=float))
    return pd.Series(out, index=close.index, name="obv")


def compute_vwap_session(candles: pd.DataFrame) -> pd.Series:
    """VWAP that resets at 00:00 UTC every day. Input DataFrame must contain
    'close_time' (tz-aware), 'high', 'low', 'close', 'volume'.
    """
    df = candles.copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    session = df["close_time"].dt.floor("D")  # reset key
    cum_vp = (typical * df["volume"]).groupby(session).cumsum()
    cum_vol = df["volume"].groupby(session).cumsum()
    vwap = cum_vp / cum_vol
    vwap.index = df.index
    return vwap.rename("vwap")


def compute_volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
    mean = volume.rolling(window=window).mean()
    std = volume.rolling(window=window).std()
    z = (volume - mean) / std.replace(0, pd.NA)
    return z.rename(f"volume_zscore_{window}")


def compute_mfi(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    out = talib.MFI(
        high.to_numpy(dtype=float), low.to_numpy(dtype=float),
        close.to_numpy(dtype=float), volume.to_numpy(dtype=float),
        timeperiod=period,
    )
    return pd.Series(out, index=high.index, name=f"mfi_{period}")
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_volume.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/indicators/volume.py tests/unit/test_indicator_volume.py
git commit -m "feat: add volume indicators (OBV, session VWAP, z-score, MFI)"
```

---

## Task 19: `indicators/structure.py` — swing H/L, pivots, prior-day/week H/L

**Files:**
- Create: `src/trading_sandwich/indicators/structure.py`
- Test: `tests/unit/test_indicator_structure.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_indicator_structure.py`:
```python
from datetime import UTC, datetime, timedelta

import pandas as pd

from trading_sandwich.indicators.structure import (
    compute_classic_pivots,
    compute_prior_day_hl,
    compute_prior_week_hl,
    compute_swing_high_low,
)


def test_swing_high_is_5_bar_fractal_peak():
    # Peak at bar 10: [1,2,3,4,5,10,5,4,3,2,1]
    highs = pd.Series([1.0, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1])
    lows  = pd.Series([0.5] * 11)
    sh, sl = compute_swing_high_low(highs, lows, lookback=5)
    # At index 10 (and onward), the most recent confirmed swing high is 10
    assert float(sh.iloc[10]) == 10.0
    # Swing low: all lows are equal; first confirmed after 5 bars
    assert sl.iloc[5:].notna().all()


def test_classic_pivots_arithmetic():
    # For a day where H=110, L=90, C=100:
    #   P  = (110 + 90 + 100) / 3                = 100
    #   R1 = 2*P - L                            = 110
    #   S1 = 2*P - H                            =  90
    #   R2 = P + (H - L)                        = 120
    #   S2 = P - (H - L)                        =  80
    p, r1, r2, s1, s2 = compute_classic_pivots(high=110, low=90, close=100)
    assert (p, r1, r2, s1, s2) == (100.0, 110.0, 120.0, 90.0, 80.0)


def test_prior_day_high_low():
    base = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    rows = []
    for day in range(3):
        for hour in range(24):
            ct = base + timedelta(days=day, hours=hour)
            close = 100 + day * 10 + hour * 0.1
            rows.append({
                "close_time": ct,
                "high": close + 0.5, "low": close - 0.5,
            })
    df = pd.DataFrame(rows)
    pdh, pdl = compute_prior_day_hl(df)
    # Day 0: 2026-04-20 highs span 100.5 → 102.8, lows 99.5 → 101.8
    # At hour 0 of day 1 (index 24), prior_day_high should be max of day 0's highs
    day0_high = max(100 + h * 0.1 + 0.5 for h in range(24))
    day0_low = min(100 + h * 0.1 - 0.5 for h in range(24))
    assert abs(float(pdh.iloc[24]) - day0_high) < 1e-9
    assert abs(float(pdl.iloc[24]) - day0_low) < 1e-9


def test_prior_week_hl_needs_full_prior_week():
    base = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)  # Monday
    rows = []
    for day in range(14):   # 2 weeks
        ct = base + timedelta(days=day, hours=12)
        rows.append({
            "close_time": ct,
            "high": 100 + day, "low": 100 - day,
        })
    df = pd.DataFrame(rows)
    pwh, pwl = compute_prior_week_hl(df)
    # At day 7 (start of week 2), prior week H/L should be max/min of days 0-6
    assert pwh.iloc[7] == 106.0
    assert pwl.iloc[7] == 94.0
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_structure.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/indicators/structure.py`:
```python
"""Price-structure features: swing H/L (fractal), classic pivots, prior-day/week H/L."""
from __future__ import annotations

import pandas as pd


def compute_swing_high_low(
    high: pd.Series, low: pd.Series, lookback: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """Most recent confirmed swing H/L using an N-bar fractal. A bar is a swing high
    if its high is greater than the high of the `lookback-1` bars before AND after.
    Forward-fill so the most recent confirmed swing H/L is carried until a new
    one appears.
    """
    half = (lookback - 1) // 2
    swing_high = pd.Series(index=high.index, dtype=float)
    swing_low = pd.Series(index=low.index, dtype=float)
    for i in range(half, len(high) - half):
        window_h = high.iloc[i - half: i + half + 1]
        window_l = low.iloc[i - half: i + half + 1]
        if float(high.iloc[i]) == float(window_h.max()) and (window_h == window_h.max()).sum() == 1:
            swing_high.iloc[i] = float(high.iloc[i])
        if float(low.iloc[i]) == float(window_l.min()) and (window_l == window_l.min()).sum() == 1:
            swing_low.iloc[i] = float(low.iloc[i])
    return (
        swing_high.ffill().rename(f"swing_high_{lookback}"),
        swing_low.ffill().rename(f"swing_low_{lookback}"),
    )


def compute_classic_pivots(
    high: float, low: float, close: float,
) -> tuple[float, float, float, float, float]:
    """Classic floor-trader pivots for one trading session."""
    p = (high + low + close) / 3.0
    r1 = 2.0 * p - low
    s1 = 2.0 * p - high
    r2 = p + (high - low)
    s2 = p - (high - low)
    return p, r1, r2, s1, s2


def compute_prior_day_hl(candles: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """At each candle, the high/low of the UTC day preceding the candle's close_time.
    Forward-filled across the day. Input must have 'close_time' (tz-aware) + 'high' + 'low'.
    """
    df = candles[["close_time", "high", "low"]].copy()
    df["day"] = df["close_time"].dt.floor("D")
    daily = df.groupby("day").agg(day_high=("high", "max"), day_low=("low", "min")).reset_index()
    daily["prior_day_high"] = daily["day_high"].shift(1)
    daily["prior_day_low"] = daily["day_low"].shift(1)
    merged = df.merge(daily[["day", "prior_day_high", "prior_day_low"]], on="day", how="left")
    merged.index = df.index
    return merged["prior_day_high"].rename("prior_day_high"), merged["prior_day_low"].rename("prior_day_low")


def compute_prior_week_hl(candles: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Prior ISO-week high/low, forward-filled across the week."""
    df = candles[["close_time", "high", "low"]].copy()
    # Week start = Monday 00:00 UTC
    df["week"] = df["close_time"].dt.to_period("W-MON").dt.start_time.dt.tz_localize("UTC")
    weekly = df.groupby("week").agg(week_high=("high", "max"), week_low=("low", "min")).reset_index()
    weekly["prior_week_high"] = weekly["week_high"].shift(1)
    weekly["prior_week_low"] = weekly["week_low"].shift(1)
    merged = df.merge(weekly[["week", "prior_week_high", "prior_week_low"]], on="week", how="left")
    merged.index = df.index
    return merged["prior_week_high"].rename("prior_week_high"), merged["prior_week_low"].rename("prior_week_low")
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_structure.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/indicators/structure.py tests/unit/test_indicator_structure.py
git commit -m "feat: add structure indicators (swing H/L, pivots, prior-day/week H/L)"
```

---

## Task 20: `indicators/microstructure.py` — funding, OI deltas, L/S ratio, OB imbalance

**Files:**
- Create: `src/trading_sandwich/indicators/microstructure.py`
- Test: `tests/unit/test_indicator_microstructure.py`

These are pure functions over raw DataFrames (funding settlements, OI snapshots, L/S samples, OB snapshots). Worker code joins them to the features pipeline; the math here is isolated.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_indicator_microstructure.py`:
```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from trading_sandwich.indicators.microstructure import (
    compute_funding_24h_mean,
    compute_ob_imbalance_05pct,
    compute_oi_deltas,
)


def test_funding_24h_mean_three_settlements():
    # Funding settles every 8h. 24h window = 3 samples.
    base = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)
    funding = pd.DataFrame([
        {"settlement_time": base,                   "rate": Decimal("0.0001")},
        {"settlement_time": base + timedelta(hours=8),  "rate": Decimal("0.0002")},
        {"settlement_time": base + timedelta(hours=16), "rate": Decimal("0.0003")},
    ])
    mean = compute_funding_24h_mean(funding, at_time=base + timedelta(hours=24))
    assert abs(float(mean) - 0.0002) < 1e-9


def test_funding_24h_mean_empty_returns_none():
    funding = pd.DataFrame(columns=["settlement_time", "rate"])
    assert compute_funding_24h_mean(funding, at_time=datetime.now(UTC)) is None


def test_oi_deltas_basic():
    base = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)
    oi = pd.DataFrame([
        {"captured_at": base - timedelta(hours=24, minutes=5), "open_interest_usd": Decimal("1_000_000_000")},
        {"captured_at": base - timedelta(hours=24),             "open_interest_usd": Decimal("1_000_000_000")},
        {"captured_at": base - timedelta(hours=1),              "open_interest_usd": Decimal("1_050_000_000")},
        {"captured_at": base,                                   "open_interest_usd": Decimal("1_100_000_000")},
    ])
    d1h, d24h = compute_oi_deltas(oi, at_time=base)
    # 1h delta = 1.1B - 1.05B = 50M
    assert abs(float(d1h) - 50_000_000) < 1e-6
    # 24h delta = 1.1B - 1.0B = 100M
    assert abs(float(d24h) - 100_000_000) < 1e-6


def test_ob_imbalance_at_0_5pct():
    """With mid = 100, 0.5% band = 99.5 … 100.5.
       Bids at [(99.8, 10), (99.2, 5)] → only 99.8 within band (size 10).
       Asks at [(100.3, 7), (100.9, 4)] → only 100.3 within band (size 7).
       Imbalance = 10 / (10 + 7) ≈ 0.588.
    """
    snap = {
        "bids": [["99.8", "10"], ["99.2", "5"]],
        "asks": [["100.3", "7"], ["100.9", "4"]],
    }
    v = compute_ob_imbalance_05pct(snap, mid_price=Decimal("100"))
    assert abs(float(v) - 10.0 / 17.0) < 1e-6


def test_ob_imbalance_empty_band_returns_half():
    # All bids and asks outside band → ambiguous, return 0.5 (neutral)
    snap = {
        "bids": [["90", "1"]],
        "asks": [["110", "1"]],
    }
    v = compute_ob_imbalance_05pct(snap, mid_price=Decimal("100"))
    assert float(v) == 0.5
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_microstructure.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/indicators/microstructure.py`:
```python
"""Futures-microstructure features: funding, open interest, L/S ratio, OB imbalance."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd


def compute_funding_24h_mean(funding: pd.DataFrame, at_time: datetime) -> Decimal | None:
    """Arithmetic mean of settled funding rates in (at_time - 24h, at_time]."""
    if funding.empty:
        return None
    window_start = at_time - timedelta(hours=24)
    mask = (funding["settlement_time"] > window_start) & (funding["settlement_time"] <= at_time)
    window = funding.loc[mask, "rate"]
    if window.empty:
        return None
    total = sum(Decimal(str(r)) for r in window)
    return total / Decimal(len(window))


def compute_oi_deltas(oi: pd.DataFrame, at_time: datetime) -> tuple[Decimal | None, Decimal | None]:
    """Return (Δ OI vs 1h ago, Δ OI vs 24h ago) in USD.
    Uses the nearest-at-or-before snapshot at each reference time.
    """
    if oi.empty:
        return None, None
    sorted_oi = oi.sort_values("captured_at")

    def _at_or_before(t: datetime) -> Decimal | None:
        mask = sorted_oi["captured_at"] <= t
        if not mask.any():
            return None
        return Decimal(str(sorted_oi.loc[mask, "open_interest_usd"].iloc[-1]))

    now_val = _at_or_before(at_time)
    if now_val is None:
        return None, None
    prev_1h = _at_or_before(at_time - timedelta(hours=1))
    prev_24h = _at_or_before(at_time - timedelta(hours=24))
    d1h = now_val - prev_1h if prev_1h is not None else None
    d24h = now_val - prev_24h if prev_24h is not None else None
    return d1h, d24h


def compute_ob_imbalance_05pct(snapshot: dict, mid_price: Decimal) -> Decimal:
    """Fraction of bid+ask depth that sits on the bid side within ±0.5% of mid.
    Input snapshot is the shape Binance emits: `{"bids": [[price, size], ...], "asks": [...]}`.
    Returns 0.5 (neutral) when the band is empty on both sides.
    """
    band_lower = mid_price * Decimal("0.995")
    band_upper = mid_price * Decimal("1.005")

    bid_depth = Decimal("0")
    for price_s, size_s in snapshot["bids"]:
        price = Decimal(str(price_s))
        if band_lower <= price <= mid_price:
            bid_depth += Decimal(str(size_s))

    ask_depth = Decimal("0")
    for price_s, size_s in snapshot["asks"]:
        price = Decimal(str(price_s))
        if mid_price < price <= band_upper:
            ask_depth += Decimal(str(size_s))

    total = bid_depth + ask_depth
    if total == 0:
        return Decimal("0.5")
    return bid_depth / total
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_microstructure.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/indicators/microstructure.py tests/unit/test_indicator_microstructure.py
git commit -m "feat: add microstructure indicators (funding, OI deltas, OB imbalance)"
```

---

## Task 21: `indicators/regime_inputs.py` — EMA-slope bps, ATR-percentile, BB-width-percentile

**Files:**
- Create: `src/trading_sandwich/indicators/regime_inputs.py`
- Test: `tests/unit/test_indicator_regime_inputs.py`

These feed the regime classifier (Task 22). Each is a pure Series → Series transform.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_indicator_regime_inputs.py`:
```python
import pandas as pd

from tests.unit._indicator_fixtures import linear_uptrend, noisy_flat
from trading_sandwich.indicators.regime_inputs import (
    compute_atr_percentile,
    compute_bb_width_percentile,
    compute_ema_slope_bps,
)


def test_ema_slope_positive_in_uptrend():
    df = linear_uptrend(n=100)
    from trading_sandwich.indicators.trend import compute_ema
    ema = compute_ema(df["close"], period=21)
    slope = compute_ema_slope_bps(ema, window=10)
    valid = slope.dropna()
    # Linear uptrend with slope 0.5/bar on close ~100 → EMA rises ~0.5/bar
    # 10-bar slope in bps ≈ (0.5 * 10 / 100) * 10_000 / 10 = 50 bps/bar
    assert valid.iloc[-1] > 0


def test_atr_percentile_bounded_0_100():
    df = linear_uptrend(n=300)
    from trading_sandwich.indicators.volatility import compute_atr
    atr = compute_atr(df["high"], df["low"], df["close"], period=14)
    pct = compute_atr_percentile(atr, window=100)
    valid = pct.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_bb_width_percentile_high_in_uptrend():
    df = linear_uptrend(n=300)
    from trading_sandwich.indicators.volatility import compute_bollinger
    _, _, _, width = compute_bollinger(df["close"], period=20, std=2)
    pct = compute_bb_width_percentile(width, window=100)
    # In a linear uptrend BB-width expands — recent percentile should be high
    assert float(pct.iloc[-1]) > 70


def test_bb_width_percentile_low_in_flat():
    df = noisy_flat(n=300)
    from trading_sandwich.indicators.volatility import compute_bollinger
    _, _, _, width = compute_bollinger(df["close"], period=20, std=2)
    pct = compute_bb_width_percentile(width, window=100)
    # Width stays tight in a flat — percentile distribution roughly uniform,
    # but last-100 mean should hover around 50 not 100
    valid = pct.dropna()
    assert float(valid.iloc[-50:].mean()) < 70
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_regime_inputs.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/indicators/regime_inputs.py`:
```python
"""Derived inputs consumed by the regime classifier. Pure functions; no
dependency on raw-market tables beyond the indicator Series they take as input.
"""
from __future__ import annotations

import pandas as pd


def compute_ema_slope_bps(ema: pd.Series, window: int = 10) -> pd.Series:
    """Slope of EMA over `window` bars, expressed in basis points per bar
    relative to the current EMA value. Positive = rising.
    """
    delta = ema - ema.shift(window)
    slope_bps_total = (delta / ema) * 10_000.0
    return (slope_bps_total / window).rename("ema_slope_bps")


def compute_atr_percentile(atr: pd.Series, window: int = 100) -> pd.Series:
    """Rolling-window percentile rank of current ATR (0-100)."""
    return (
        atr.rolling(window=window).rank(pct=True) * 100.0
    ).rename(f"atr_percentile_{window}")


def compute_bb_width_percentile(bb_width: pd.Series, window: int = 100) -> pd.Series:
    """Rolling-window percentile rank of current BB-width (0-100)."""
    return (
        bb_width.rolling(window=window).rank(pct=True) * 100.0
    ).rename(f"bb_width_percentile_{window}")
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_indicator_regime_inputs.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/indicators/regime_inputs.py tests/unit/test_indicator_regime_inputs.py
git commit -m "feat: add regime-classifier inputs (EMA slope bps, ATR/BB-width percentiles)"
```

---

## Task 22: Regime classifier

**Files:**
- Create: `src/trading_sandwich/regime/__init__.py`
- Create: `src/trading_sandwich/regime/classifier.py`
- Test: `tests/unit/test_regime_classifier.py`

Rule-based classifier (spec §4). Inputs: per-candle close, EMA-55, EMA-21-slope-bps, ADX-14, BB-width-percentile. Outputs: `(trend_regime, vol_regime)`.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_regime_classifier.py`:
```python
from decimal import Decimal

from trading_sandwich.regime.classifier import classify


# Default thresholds from policy.yaml:
#   trend_slope_threshold_bps: 2.0
#   adx_trend_threshold: 20
#   squeeze_percentile: 20
#   expansion_percentile: 80
_POLICY = {
    "trend_slope_threshold_bps": 2.0,
    "adx_trend_threshold": 20,
    "squeeze_percentile": 20,
    "expansion_percentile": 80,
}


def test_trend_up_strict():
    trend, vol = classify(
        close=Decimal("101"), ema_55=Decimal("100"),
        ema_slope_bps=3.0, adx=25.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "trend_up"
    assert vol == "normal"


def test_trend_down_strict():
    trend, _ = classify(
        close=Decimal("99"), ema_55=Decimal("100"),
        ema_slope_bps=-3.0, adx=25.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "trend_down"


def test_range_when_adx_below_threshold():
    trend, _ = classify(
        close=Decimal("101"), ema_55=Decimal("100"),
        ema_slope_bps=3.0, adx=15.0,   # ADX < 20 kills trend label
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "range"


def test_range_when_slope_below_threshold():
    trend, _ = classify(
        close=Decimal("101"), ema_55=Decimal("100"),
        ema_slope_bps=1.0,             # < 2.0 threshold
        adx=25.0,
        bb_width_percentile_100=50.0,
        policy=_POLICY,
    )
    assert trend == "range"


def test_squeeze_vol_regime():
    _, vol = classify(
        close=Decimal("100"), ema_55=Decimal("100"),
        ema_slope_bps=0.0, adx=15.0,
        bb_width_percentile_100=10.0,   # < 20 squeeze percentile
        policy=_POLICY,
    )
    assert vol == "squeeze"


def test_expansion_vol_regime():
    _, vol = classify(
        close=Decimal("100"), ema_55=Decimal("100"),
        ema_slope_bps=0.0, adx=15.0,
        bb_width_percentile_100=85.0,   # > 80 expansion percentile
        policy=_POLICY,
    )
    assert vol == "expansion"


def test_returns_range_normal_when_any_input_none():
    trend, vol = classify(
        close=Decimal("100"), ema_55=None,
        ema_slope_bps=None, adx=None,
        bb_width_percentile_100=None,
        policy=_POLICY,
    )
    assert (trend, vol) == ("range", "normal")
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_regime_classifier.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/regime/__init__.py` (empty).

Create `src/trading_sandwich/regime/classifier.py`:
```python
"""Rule-based regime classifier. Maps per-candle indicator values to two
independent labels: trend_regime ∈ {trend_up, trend_down, range} and
vol_regime ∈ {squeeze, normal, expansion}.

Thresholds come from `policy.yaml` so they're tunable without code changes.
The Phase 1 defaults are deliberately conservative; tune once ≥2 weeks of
live data accumulate.
"""
from __future__ import annotations

from decimal import Decimal

TrendRegime = str  # Literal["trend_up", "trend_down", "range"]
VolRegime = str    # Literal["squeeze", "normal", "expansion"]


def classify(
    *,
    close: Decimal | None,
    ema_55: Decimal | None,
    ema_slope_bps: float | None,
    adx: float | None,
    bb_width_percentile_100: float | None,
    policy: dict,
) -> tuple[TrendRegime, VolRegime]:
    """Return (trend_regime, vol_regime) for one candle.

    Falls back to ('range', 'normal') when any input needed for a label is None
    (warmup periods, missing microstructure, etc.). This keeps the downstream
    detector gating deterministic: untyped candles get the most conservative
    label.
    """
    trend = _classify_trend(
        close=close, ema_55=ema_55,
        ema_slope_bps=ema_slope_bps, adx=adx,
        policy=policy,
    )
    vol = _classify_vol(
        bb_width_percentile_100=bb_width_percentile_100,
        policy=policy,
    )
    return trend, vol


def _classify_trend(
    *,
    close: Decimal | None, ema_55: Decimal | None,
    ema_slope_bps: float | None, adx: float | None,
    policy: dict,
) -> TrendRegime:
    if close is None or ema_55 is None or ema_slope_bps is None or adx is None:
        return "range"

    slope_threshold = float(policy["trend_slope_threshold_bps"])
    adx_threshold = float(policy["adx_trend_threshold"])

    if adx < adx_threshold:
        return "range"

    if close > ema_55 and ema_slope_bps > slope_threshold:
        return "trend_up"
    if close < ema_55 and ema_slope_bps < -slope_threshold:
        return "trend_down"
    return "range"


def _classify_vol(
    *,
    bb_width_percentile_100: float | None,
    policy: dict,
) -> VolRegime:
    if bb_width_percentile_100 is None:
        return "normal"

    squeeze_cutoff = float(policy["squeeze_percentile"])
    expansion_cutoff = float(policy["expansion_percentile"])

    if bb_width_percentile_100 < squeeze_cutoff:
        return "squeeze"
    if bb_width_percentile_100 > expansion_cutoff:
        return "expansion"
    return "normal"
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_regime_classifier.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/regime/ tests/unit/test_regime_classifier.py
git commit -m "feat: add rule-based regime classifier (trend + vol)"
```

---

## Task 23: Binance REST poller — funding, OI, L/S ratio

**Files:**
- Create: `src/trading_sandwich/ingestor/rest_poller.py`
- Test: `tests/unit/test_rest_poller.py`

REST poller is a thin async wrapper over Binance's public futures endpoints. Tests mock HTTP with `httpx.MockTransport`; no real network in unit tests.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_rest_poller.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from trading_sandwich.ingestor.rest_poller import (
    fetch_funding_rate,
    fetch_long_short_ratio,
    fetch_open_interest,
)


class _MockTransport(httpx.MockTransport):
    """Preloaded JSON responses keyed by URL path."""
    def __init__(self, routes: dict[str, list]):
        def handler(request: httpx.Request) -> httpx.Response:
            body = routes[request.url.path]
            return httpx.Response(200, json=body)
        super().__init__(handler)


@pytest.mark.asyncio
async def test_fetch_funding_rate():
    transport = _MockTransport({
        "/fapi/v1/fundingRate": [
            {"symbol": "BTCUSDT", "fundingTime": 1734566400000, "fundingRate": "0.00012"},
            {"symbol": "BTCUSDT", "fundingTime": 1734595200000, "fundingRate": "0.00015"},
        ],
    })
    async with httpx.AsyncClient(transport=transport, base_url="https://fapi.binance.com") as client:
        rows = await fetch_funding_rate(client, symbol="BTCUSDT", limit=2)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["rate"] == Decimal("0.00012")
    assert isinstance(rows[0]["settlement_time"], datetime)
    assert rows[0]["settlement_time"].tzinfo == UTC


@pytest.mark.asyncio
async def test_fetch_open_interest():
    transport = _MockTransport({
        "/fapi/v1/openInterest": {
            "openInterest": "123456.789", "symbol": "BTCUSDT", "time": 1734595200000,
        },
    })
    async with httpx.AsyncClient(transport=transport, base_url="https://fapi.binance.com") as client:
        row = await fetch_open_interest(client, symbol="BTCUSDT", mark_price=Decimal("100000"))
    # open_interest_usd = 123456.789 * 100000 mark
    assert row["symbol"] == "BTCUSDT"
    assert row["open_interest_usd"] == Decimal("12345678900.000")
    assert row["captured_at"].tzinfo == UTC


@pytest.mark.asyncio
async def test_fetch_long_short_ratio():
    transport = _MockTransport({
        "/futures/data/topLongShortAccountRatio": [
            {"symbol": "BTCUSDT", "longShortRatio": "1.5", "timestamp": 1734595200000,
             "longAccount": "0.6", "shortAccount": "0.4"},
        ],
    })
    async with httpx.AsyncClient(transport=transport, base_url="https://fapi.binance.com") as client:
        rows = await fetch_long_short_ratio(client, symbol="BTCUSDT", period="5m", limit=1)
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["ratio"] == Decimal("1.5")
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_rest_poller.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/ingestor/rest_poller.py`:
```python
"""Async Binance USD-M futures REST fetchers. Returns normalized dicts ready
for INSERT into raw_funding / raw_open_interest / raw_long_short_ratio.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

_FAPI_BASE = "https://fapi.binance.com"


async def fetch_funding_rate(
    client: httpx.AsyncClient, *, symbol: str, limit: int = 100,
) -> list[dict]:
    """GET /fapi/v1/fundingRate?symbol=<symbol>&limit=<limit>.
    Returns rows sorted by settlement_time ascending.
    """
    resp = await client.get("/fapi/v1/fundingRate", params={"symbol": symbol, "limit": limit})
    resp.raise_for_status()
    rows = [
        {
            "symbol": r["symbol"],
            "settlement_time": datetime.fromtimestamp(r["fundingTime"] / 1000, tz=UTC),
            "rate": Decimal(str(r["fundingRate"])),
        }
        for r in resp.json()
    ]
    rows.sort(key=lambda x: x["settlement_time"])
    return rows


async def fetch_open_interest(
    client: httpx.AsyncClient, *, symbol: str, mark_price: Decimal,
) -> dict:
    """GET /fapi/v1/openInterest?symbol=<symbol>.
    Multiplies contracts by `mark_price` to store USD value.
    """
    resp = await client.get("/fapi/v1/openInterest", params={"symbol": symbol})
    resp.raise_for_status()
    data = resp.json()
    contracts = Decimal(str(data["openInterest"]))
    return {
        "symbol": data["symbol"],
        "captured_at": datetime.fromtimestamp(data["time"] / 1000, tz=UTC),
        "open_interest_usd": (contracts * mark_price).quantize(Decimal("0.001")),
    }


async def fetch_long_short_ratio(
    client: httpx.AsyncClient, *, symbol: str, period: str = "5m", limit: int = 30,
) -> list[dict]:
    """GET /futures/data/topLongShortAccountRatio?symbol=<symbol>&period=<period>&limit=<limit>."""
    resp = await client.get(
        "/futures/data/topLongShortAccountRatio",
        params={"symbol": symbol, "period": period, "limit": limit},
    )
    resp.raise_for_status()
    rows = [
        {
            "symbol": r["symbol"],
            "captured_at": datetime.fromtimestamp(r["timestamp"] / 1000, tz=UTC),
            "ratio": Decimal(str(r["longShortRatio"])),
        }
        for r in resp.json()
    ]
    rows.sort(key=lambda x: x["captured_at"])
    return rows


def fapi_base_url() -> str:
    """Exposed as a helper so beat jobs can build one `httpx.AsyncClient` per invocation."""
    return _FAPI_BASE
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_rest_poller.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/ingestor/rest_poller.py tests/unit/test_rest_poller.py
git commit -m "feat: add Binance REST pollers (funding, OI, L/S ratio)"
```

---

## Task 24: Celery Beat jobs — funding/OI/LSR pollers

**Files:**
- Modify: `src/trading_sandwich/celery_app.py` — register beat entries
- Create: `src/trading_sandwich/ingestor/rest_tasks.py` — @app.task bodies
- Test: `tests/integration/test_rest_tasks.py`

Beat fires the pollers on cadence (funding 1/min, OI 1/5min, LSR 1/5min). Each task writes to the appropriate raw table.

- [ ] **Step 1: Write failing integration test**

Create `tests/integration/test_rest_tasks.py`:
```python
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


def _select_count(async_url: str, table: str) -> int:
    async def _run() -> int:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_poll_funding_writes_rows(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)
        command.upgrade(Config("alembic.ini"), "head")

        # Stub the fetcher so no real network is used
        from datetime import UTC, datetime
        stub_rows = [
            {"symbol": "BTCUSDT",
             "settlement_time": datetime(2026, 4, 21, 0, tzinfo=UTC),
             "rate": Decimal("0.0001")},
            {"symbol": "BTCUSDT",
             "settlement_time": datetime(2026, 4, 21, 8, tzinfo=UTC),
             "rate": Decimal("0.00015")},
        ]
        with patch(
            "trading_sandwich.ingestor.rest_tasks.fetch_funding_rate",
            new=AsyncMock(return_value=stub_rows),
        ):
            from trading_sandwich.ingestor.rest_tasks import poll_funding
            poll_funding.run("BTCUSDT")

        assert _select_count(pg_url, "raw_funding") == 2


@pytest.mark.integration
def test_poll_open_interest_writes_row(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)
        command.upgrade(Config("alembic.ini"), "head")

        from datetime import UTC, datetime
        stub_row = {
            "symbol": "BTCUSDT",
            "captured_at": datetime(2026, 4, 21, 12, tzinfo=UTC),
            "open_interest_usd": Decimal("12345678900"),
        }
        with (
            patch(
                "trading_sandwich.ingestor.rest_tasks.fetch_open_interest",
                new=AsyncMock(return_value=stub_row),
            ),
            patch(
                "trading_sandwich.ingestor.rest_tasks._latest_mark_price",
                new=AsyncMock(return_value=Decimal("100000")),
            ),
        ):
            from trading_sandwich.ingestor.rest_tasks import poll_open_interest
            poll_open_interest.run("BTCUSDT")

        assert _select_count(pg_url, "raw_open_interest") == 1
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_rest_tasks.py -v -m integration`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement rest_tasks**

Create `src/trading_sandwich/ingestor/rest_tasks.py`:
```python
"""Celery tasks wrapping the REST pollers. Called on cadence by Celery Beat
(schedule configured in celery_app.py).
"""
from __future__ import annotations

from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import RawFunding, RawLongShortRatio, RawOpenInterest
from trading_sandwich.ingestor.rest_poller import (
    fapi_base_url,
    fetch_funding_rate,
    fetch_long_short_ratio,
    fetch_open_interest,
)
from trading_sandwich.logging import get_logger

logger = get_logger(__name__)


async def _latest_mark_price(client: httpx.AsyncClient, symbol: str) -> Decimal:
    resp = await client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
    resp.raise_for_status()
    return Decimal(str(resp.json()["markPrice"]))


async def _persist(model, rows: list[dict] | dict) -> None:
    if not rows:
        return
    if isinstance(rows, dict):
        rows = [rows]
    session_factory = get_session_factory()
    async with session_factory() as session:
        for row in rows:
            stmt = pg_insert(model).values(**row).on_conflict_do_nothing()
            await session.execute(stmt)
        await session.commit()


async def _poll_funding_async(symbol: str) -> None:
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=10.0) as client:
        rows = await fetch_funding_rate(client, symbol=symbol, limit=100)
    await _persist(RawFunding, rows)
    logger.info("poll_funding_done", symbol=symbol, rows=len(rows))


async def _poll_open_interest_async(symbol: str) -> None:
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=10.0) as client:
        mark = await _latest_mark_price(client, symbol)
        row = await fetch_open_interest(client, symbol=symbol, mark_price=mark)
    await _persist(RawOpenInterest, row)
    logger.info("poll_oi_done", symbol=symbol)


async def _poll_long_short_ratio_async(symbol: str) -> None:
    async with httpx.AsyncClient(base_url=fapi_base_url(), timeout=10.0) as client:
        rows = await fetch_long_short_ratio(client, symbol=symbol, period="5m", limit=30)
    await _persist(RawLongShortRatio, rows)
    logger.info("poll_lsr_done", symbol=symbol, rows=len(rows))


@app.task(name="trading_sandwich.ingestor.rest_tasks.poll_funding")
def poll_funding(symbol: str) -> None:
    run_coro(_poll_funding_async(symbol))


@app.task(name="trading_sandwich.ingestor.rest_tasks.poll_open_interest")
def poll_open_interest(symbol: str) -> None:
    run_coro(_poll_open_interest_async(symbol))


@app.task(name="trading_sandwich.ingestor.rest_tasks.poll_long_short_ratio")
def poll_long_short_ratio(symbol: str) -> None:
    run_coro(_poll_long_short_ratio_async(symbol))
```

- [ ] **Step 4: Register the Beat schedule**

In `src/trading_sandwich/celery_app.py`, update the `include=` list of the Celery() constructor to add `"trading_sandwich.ingestor.rest_tasks"`.

Update the `beat_schedule` in `app.conf.update(...)`:
```python
    beat_schedule={
        # Microstructure pollers (one entry per symbol × task — expanded at import time).
        **{
            f"poll_funding_{s}": {
                "task": "trading_sandwich.ingestor.rest_tasks.poll_funding",
                "schedule": 60.0,
                "args": [s],
            }
            for s in _universe_symbols()
        },
        **{
            f"poll_oi_{s}": {
                "task": "trading_sandwich.ingestor.rest_tasks.poll_open_interest",
                "schedule": 300.0,
                "args": [s],
            }
            for s in _universe_symbols()
        },
        **{
            f"poll_lsr_{s}": {
                "task": "trading_sandwich.ingestor.rest_tasks.poll_long_short_ratio",
                "schedule": 300.0,
                "args": [s],
            }
            for s in _universe_symbols()
        },
    },
```

And at the top of `celery_app.py` (before `app = Celery(...)`), add:
```python
def _universe_symbols() -> list[str]:
    """Read universe from policy.yaml. Local helper so celery_app.py doesn't
    import trading_sandwich._universe (which would create a circular import
    chain once _universe grows).
    """
    import yaml
    from pathlib import Path
    try:
        with open(Path("policy.yaml")) as f:
            return list(yaml.safe_load(f)["universe"])
    except FileNotFoundError:
        return ["BTCUSDT", "ETHUSDT"]
```

- [ ] **Step 5: Run integration test**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_rest_tasks.py -v -m integration`
Expected: all PASS.

- [ ] **Step 6: Ensure full test suite green**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/trading_sandwich/ingestor/rest_tasks.py src/trading_sandwich/celery_app.py tests/integration/test_rest_tasks.py
git commit -m "feat: Celery Beat jobs for funding/OI/LSR pollers"
```

---

## Task 25: Binance L2 depth ingestor

**Files:**
- Create: `src/trading_sandwich/ingestor/binance_depth_stream.py`
- Test: `tests/unit/test_binance_depth_stream.py`

Subscribes to `<symbol>@depth20@100ms` streams via CCXT Pro, normalizes into snapshots at most every 200ms, writes to `raw_orderbook_snapshots`. Runs inside the existing ingestor service or a sibling one — decision deferred to Task 47.

- [ ] **Step 1: Write failing unit test**

Create `tests/unit/test_binance_depth_stream.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

from trading_sandwich.ingestor.binance_depth_stream import normalize_ccxt_depth


def test_normalize_ccxt_depth():
    raw = {
        "symbol": "BTC/USDT",
        "bids": [["99.8", "10"], ["99.5", "7"]],
        "asks": [["100.2", "5"], ["100.5", "12"]],
        "timestamp": 1734595200000,  # 2024-12-19 08:00:00 UTC
    }
    snap = normalize_ccxt_depth("BTCUSDT", raw)
    assert snap["symbol"] == "BTCUSDT"
    assert snap["captured_at"] == datetime.fromtimestamp(1734595200.0, tz=UTC)
    # Depth is stored as list[list[str]] (JSON-serialisable)
    assert snap["bids"][0] == ["99.8", "10"]
    assert snap["asks"][0] == ["100.2", "5"]


def test_normalize_ccxt_depth_uses_now_if_no_timestamp():
    raw = {"symbol": "BTC/USDT", "bids": [], "asks": [], "timestamp": None}
    snap = normalize_ccxt_depth("BTCUSDT", raw)
    assert abs((datetime.now(UTC) - snap["captured_at"]).total_seconds()) < 5
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_binance_depth_stream.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/ingestor/binance_depth_stream.py`:
```python
"""CCXT Pro adapter for Binance L2 depth streams. Normalizes updates into
`raw_orderbook_snapshots` row dicts at most every `throttle_ms`.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import ccxt.pro as ccxtpro

from trading_sandwich.logging import get_logger

logger = get_logger(__name__)


def normalize_ccxt_depth(symbol: str, raw: dict) -> dict:
    """Raw CCXT Pro depth → persistable snapshot dict.
    Levels are preserved as list[list[str]] so Postgres JSONB round-trips
    cleanly and Decimal conversion is deferred to the feature-worker.
    """
    ts_ms = raw.get("timestamp")
    if ts_ms is None:
        captured_at = datetime.now(UTC)
    else:
        captured_at = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    return {
        "symbol": symbol,
        "captured_at": captured_at,
        "bids": [[str(p), str(s)] for p, s in raw.get("bids", [])[:20]],
        "asks": [[str(p), str(s)] for p, s in raw.get("asks", [])[:20]],
    }


async def stream_depth(
    symbols: list[str],
    *,
    testnet: bool = False,
    throttle_ms: int = 200,
) -> AsyncIterator[dict]:
    """Yield normalized depth snapshots at most `throttle_ms` apart per symbol.
    CCXT Pro's `watch_order_book_for_symbols` keeps an in-memory book that
    updates on every delta; we emit the 20-level head snapshot at a steady
    cadence rather than on every tick.
    """
    exchange = ccxtpro.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    if testnet:
        exchange.set_sandbox_mode(True)

    last_emit: dict[str, float] = {s: 0.0 for s in symbols}
    throttle_s = throttle_ms / 1000.0

    try:
        while True:
            try:
                ob = await exchange.watch_order_book_for_symbols(
                    [f"{s[:-4]}/{s[-4:]}" for s in symbols], limit=20,
                )
            except Exception as e:
                logger.exception("ws_depth_error", err=str(e))
                await asyncio.sleep(2)
                continue

            ccxt_symbol = ob["symbol"]
            underscore_symbol = ccxt_symbol.replace("/", "")
            now = asyncio.get_event_loop().time()
            if now - last_emit.get(underscore_symbol, 0.0) < throttle_s:
                continue
            last_emit[underscore_symbol] = now
            yield normalize_ccxt_depth(underscore_symbol, ob)
    finally:
        await exchange.close()
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_binance_depth_stream.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/ingestor/binance_depth_stream.py tests/unit/test_binance_depth_stream.py
git commit -m "feat: add CCXT Pro L2 depth stream adapter + normalizer"
```

---

# Checkpoint G — pause for human review

Tasks 11–25 complete. Migrations 0008–0009 applied; `raw_candles` now partitioned; `signals.archetype` CHECK constraint enforced. Policy loader, universe helper, 6 indicator modules + 1 regime-inputs module live; all indicator families unit-tested. Regime classifier tested. REST poller + Celery Beat jobs green. L2 depth normalizer unit-tested.

**Before continuing to Checkpoint H, verify manually:**
```bash
docker compose config --quiet
MSYS_NO_PATHCONV=1 docker compose run --rm tools ruff check src tests
MSYS_NO_PATHCONV=1 docker compose run --rm test -q
```

All three should be green.

---

## Task 26: Feature-worker overhaul — orchestrate the full indicator stack

**Files:**
- Modify: `src/trading_sandwich/features/compute.py` — rewrite as an orchestrator
- Modify: `src/trading_sandwich/features/worker.py` — call the orchestrator, pass extra raw-table reads
- Test: `tests/integration/test_features_full_row.py`

The Phase 0 `compute.py` has three functions (`compute_ema`, `compute_rsi`, `compute_atr`). Phase 1 replaces its body with an orchestrator that: (1) reads raw_candles, raw_orderbook_snapshots, raw_funding, raw_open_interest, raw_long_short_ratio, (2) computes the full indicator stack, (3) computes regime-classifier inputs, (4) runs the classifier, (5) returns a dict keyed by the 48 Phase 1 column names plus `trend_regime` / `vol_regime`.

- [ ] **Step 1: Write failing integration test**

Create `tests/integration/test_features_full_row.py`:
```python
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


_REQUIRED_NON_NULL = [
    "close_price", "ema_21", "rsi_14", "atr_14",
    "ema_8", "ema_55",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
    "trend_regime", "vol_regime",
]


def _seed_candles(async_url: str, n: int = 250) -> datetime:
    """Seed n 5m candles rising linearly from 100 to 100+n*0.5."""
    base = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                for i in range(n):
                    c = 100.0 + i * 0.5
                    ot = base + timedelta(minutes=5 * i)
                    ct = ot + timedelta(minutes=5)
                    await conn.execute(text(
                        "INSERT INTO raw_candles "
                        "(symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                        "VALUES (:s,:tf,:ot,:ct,:o,:h,:l,:c,10)"
                    ), {"s": "BTCUSDT", "tf": "5m", "ot": ot, "ct": ct,
                        "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base


def _latest_features(async_url: str) -> dict:
    async def _run() -> dict:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                cols = ", ".join(_REQUIRED_NON_NULL)
                row = (await conn.execute(text(
                    f"SELECT {cols} FROM features "
                    "WHERE symbol='BTCUSDT' AND timeframe='5m' "
                    "ORDER BY close_time DESC LIMIT 1"
                ))).one()
                return {k: getattr(row, k) for k in _REQUIRED_NON_NULL}
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
def test_features_row_populates_all_phase_1_columns(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)

        command.upgrade(Config("alembic.ini"), "head")
        base = _seed_candles(pg_url, n=250)

        from trading_sandwich.features.worker import compute_features
        close_iso = (base + timedelta(minutes=5 * 250)).isoformat()
        compute_features.run("BTCUSDT", "5m", close_iso)

        row = _latest_features(pg_url)
        for col in _REQUIRED_NON_NULL:
            assert row[col] is not None, f"{col} should be non-null after 250-bar warmup"
        # Linear uptrend → trend_up + normal (no squeeze, no expansion extreme)
        assert row["trend_regime"] == "trend_up"
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_features_full_row.py -v -m integration`
Expected: FAIL — Phase 0's worker only populates `ema_21`, `rsi_14`, `atr_14`, leaves Phase 1 columns NULL.

- [ ] **Step 3: Rewrite `features/compute.py`**

Replace the contents of `src/trading_sandwich/features/compute.py` with:
```python
"""Feature orchestrator. Pulls raw-table inputs, runs every indicator module,
applies the regime classifier, returns a dict keyed by `features` table columns.

Phase 0's 3-function API (compute_ema/compute_rsi/compute_atr) is preserved
via re-exports from the indicator package so any remaining Phase 0 callers
keep working without an import change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pandas as pd

from trading_sandwich._policy import get_regime_thresholds
from trading_sandwich.indicators.microstructure import (
    compute_funding_24h_mean,
    compute_ob_imbalance_05pct,
    compute_oi_deltas,
)
from trading_sandwich.indicators.regime_inputs import (
    compute_atr_percentile,
    compute_bb_width_percentile,
    compute_ema_slope_bps,
)
from trading_sandwich.indicators.structure import (
    compute_classic_pivots,
    compute_prior_day_hl,
    compute_prior_week_hl,
    compute_swing_high_low,
)
from trading_sandwich.indicators.trend import (
    compute_adx,
    compute_ema,
    compute_macd,
    compute_roc,
    compute_rsi,
    compute_stoch_rsi,
)
from trading_sandwich.indicators.volatility import (
    compute_atr,
    compute_bollinger,
    compute_donchian,
    compute_keltner,
)
from trading_sandwich.indicators.volume import (
    compute_mfi,
    compute_obv,
    compute_volume_zscore,
    compute_vwap_session,
)
from trading_sandwich.regime.classifier import classify

__all__ = [
    "compute_ema", "compute_rsi", "compute_atr",   # Phase 0 re-exports
    "build_features_row",
]


@dataclass
class RawInputs:
    """Everything the orchestrator needs at compute time. Assembled by the
    worker before calling `build_features_row`.
    """
    candles: pd.DataFrame            # >=200 bars, OHLCV + close_time
    funding: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["settlement_time", "rate"]))
    open_interest: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["captured_at", "open_interest_usd"]))
    long_short_ratio: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["captured_at", "ratio"]))
    latest_ob_snapshot: dict | None = None


def build_features_row(
    symbol: str, timeframe: str, close_time: datetime,
    inputs: RawInputs,
) -> dict | None:
    """Compute every Phase 1 indicator + regime label for the most-recent
    candle (the one whose close_time matches `close_time`). Returns a dict
    with keys matching the `features` table columns, or None if insufficient
    history.
    """
    df = inputs.candles
    if len(df) < 200:
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    # --- Trend + momentum ---
    ema_8 = compute_ema(close, 8)
    ema_21 = compute_ema(close, 21)
    ema_55 = compute_ema(close, 55)
    ema_200 = compute_ema(close, 200)
    rsi_14 = compute_rsi(close, 14)
    macd_line, macd_signal_s, macd_hist = compute_macd(close)
    adx_14, di_plus_14, di_minus_14 = compute_adx(high, low, close, 14)
    stoch_k, stoch_d = compute_stoch_rsi(close)
    roc_10 = compute_roc(close, 10)

    # --- Volatility + range ---
    atr_14 = compute_atr(high, low, close, 14)
    bb_up, bb_mid, bb_lo, bb_w = compute_bollinger(close, 20, 2.0)
    kc_up, kc_mid, kc_lo = compute_keltner(high, low, close, 20, 2.0)
    dc_up, dc_mid, dc_lo = compute_donchian(high, low, 20)

    # --- Volume + flow ---
    obv = compute_obv(close, volume)
    vwap = compute_vwap_session(df)
    vol_z = compute_volume_zscore(volume, 20)
    mfi_14 = compute_mfi(high, low, close, volume, 14)

    # --- Structure ---
    swing_h, swing_l = compute_swing_high_low(high, low, 5)
    pdh, pdl = compute_prior_day_hl(df)
    pwh, pwl = compute_prior_week_hl(df)
    # Classic pivots for today use the previous-day H/L/close values
    prev_close_row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    p_p, p_r1, p_r2, p_s1, p_s2 = compute_classic_pivots(
        high=float(pdh.iloc[-1]) if pd.notna(pdh.iloc[-1]) else float(prev_close_row["high"]),
        low=float(pdl.iloc[-1]) if pd.notna(pdl.iloc[-1]) else float(prev_close_row["low"]),
        close=float(prev_close_row["close"]),
    )

    # --- Regime inputs ---
    slope_bps = compute_ema_slope_bps(ema_21, window=10)
    atr_pct_100 = compute_atr_percentile(atr_14, window=100)
    bbw_pct_100 = compute_bb_width_percentile(bb_w, window=100)

    # --- Microstructure ---
    fr_24h_mean = compute_funding_24h_mean(inputs.funding, close_time)
    latest_funding_rate = (
        Decimal(str(inputs.funding["rate"].iloc[-1]))
        if not inputs.funding.empty else None
    )
    latest_oi_usd = (
        Decimal(str(inputs.open_interest["open_interest_usd"].iloc[-1]))
        if not inputs.open_interest.empty else None
    )
    d_oi_1h, d_oi_24h = compute_oi_deltas(inputs.open_interest, close_time)
    latest_lsr = (
        Decimal(str(inputs.long_short_ratio["ratio"].iloc[-1]))
        if not inputs.long_short_ratio.empty else None
    )
    ob_imb = None
    if inputs.latest_ob_snapshot is not None and pd.notna(bb_mid.iloc[-1]):
        ob_imb = compute_ob_imbalance_05pct(
            inputs.latest_ob_snapshot, Decimal(str(close.iloc[-1])),
        )

    # --- Regime classification (same last-bar values feeding the detectors) ---
    trend_regime, vol_regime = classify(
        close=Decimal(str(close.iloc[-1])),
        ema_55=_dec_or_none(ema_55.iloc[-1]),
        ema_slope_bps=_float_or_none(slope_bps.iloc[-1]),
        adx=_float_or_none(adx_14.iloc[-1]),
        bb_width_percentile_100=_float_or_none(bbw_pct_100.iloc[-1]),
        policy=get_regime_thresholds(),
    )

    return {
        "symbol": symbol, "timeframe": timeframe, "close_time": close_time,
        "close_price": Decimal(str(close.iloc[-1])),
        "ema_8":   _dec_or_none(ema_8.iloc[-1]),
        "ema_21":  _dec_or_none(ema_21.iloc[-1]),
        "ema_55":  _dec_or_none(ema_55.iloc[-1]),
        "ema_200": _dec_or_none(ema_200.iloc[-1]),
        "rsi_14":  _dec_or_none(rsi_14.iloc[-1]),
        "atr_14":  _dec_or_none(atr_14.iloc[-1]),
        "macd_line":   _dec_or_none(macd_line.iloc[-1]),
        "macd_signal": _dec_or_none(macd_signal_s.iloc[-1]),
        "macd_hist":   _dec_or_none(macd_hist.iloc[-1]),
        "adx_14":      _dec_or_none(adx_14.iloc[-1]),
        "di_plus_14":  _dec_or_none(di_plus_14.iloc[-1]),
        "di_minus_14": _dec_or_none(di_minus_14.iloc[-1]),
        "stoch_rsi_k": _dec_or_none(stoch_k.iloc[-1]),
        "stoch_rsi_d": _dec_or_none(stoch_d.iloc[-1]),
        "roc_10":      _dec_or_none(roc_10.iloc[-1]),
        "bb_upper":    _dec_or_none(bb_up.iloc[-1]),
        "bb_middle":   _dec_or_none(bb_mid.iloc[-1]),
        "bb_lower":    _dec_or_none(bb_lo.iloc[-1]),
        "bb_width":    _dec_or_none(bb_w.iloc[-1]),
        "keltner_upper":  _dec_or_none(kc_up.iloc[-1]),
        "keltner_middle": _dec_or_none(kc_mid.iloc[-1]),
        "keltner_lower":  _dec_or_none(kc_lo.iloc[-1]),
        "donchian_upper":  _dec_or_none(dc_up.iloc[-1]),
        "donchian_middle": _dec_or_none(dc_mid.iloc[-1]),
        "donchian_lower":  _dec_or_none(dc_lo.iloc[-1]),
        "obv":              _dec_or_none(obv.iloc[-1]),
        "vwap":             _dec_or_none(vwap.iloc[-1]),
        "volume_zscore_20": _dec_or_none(vol_z.iloc[-1]),
        "mfi_14":           _dec_or_none(mfi_14.iloc[-1]),
        "swing_high_5":     _dec_or_none(swing_h.iloc[-1]),
        "swing_low_5":      _dec_or_none(swing_l.iloc[-1]),
        "pivot_p":  Decimal(str(p_p)),
        "pivot_r1": Decimal(str(p_r1)),
        "pivot_r2": Decimal(str(p_r2)),
        "pivot_s1": Decimal(str(p_s1)),
        "pivot_s2": Decimal(str(p_s2)),
        "prior_day_high":  _dec_or_none(pdh.iloc[-1]),
        "prior_day_low":   _dec_or_none(pdl.iloc[-1]),
        "prior_week_high": _dec_or_none(pwh.iloc[-1]),
        "prior_week_low":  _dec_or_none(pwl.iloc[-1]),
        "funding_rate":          latest_funding_rate,
        "funding_rate_24h_mean": fr_24h_mean,
        "open_interest_usd": latest_oi_usd,
        "oi_delta_1h":       d_oi_1h,
        "oi_delta_24h":      d_oi_24h,
        "long_short_ratio":  latest_lsr,
        "ob_imbalance_05":   ob_imb,
        "ema_21_slope_bps":         _dec_or_none(slope_bps.iloc[-1]),
        "atr_percentile_100":       _dec_or_none(atr_pct_100.iloc[-1]),
        "bb_width_percentile_100":  _dec_or_none(bbw_pct_100.iloc[-1]),
        "trend_regime": trend_regime,
        "vol_regime":   vol_regime,
    }


def _dec_or_none(x) -> Decimal | None:
    if pd.isna(x):
        return None
    return Decimal(str(float(x)))


def _float_or_none(x) -> float | None:
    if pd.isna(x):
        return None
    return float(x)
```

- [ ] **Step 4: Rewrite `features/worker.py` to load all raw tables and call the orchestrator**

Replace the body of `src/trading_sandwich/features/worker.py` with:
```python
"""Feature worker. Celery consumer that assembles RawInputs from all the
raw-data tables, invokes the orchestrator, upserts a features row, and
dispatches signal detection.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import (
    Features,
    RawCandle,
    RawFunding,
    RawLongShortRatio,
    RawOpenInterest,
    RawOrderbookSnapshot,
)
from trading_sandwich.features.compute import RawInputs, build_features_row
from trading_sandwich.logging import get_logger
from trading_sandwich.metrics import FEATURE_COMPUTE_SECONDS, FEATURES_COMPUTED

logger = get_logger(__name__)

WINDOW_SIZE = 500


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_FEATURE_VERSION = _git_sha()


async def _load_raw_inputs(
    session_factory, symbol: str, timeframe: str, close_time: datetime,
) -> RawInputs | None:
    async with session_factory() as session:
        candle_rows = (await session.execute(
            select(RawCandle)
            .where(
                RawCandle.symbol == symbol,
                RawCandle.timeframe == timeframe,
                RawCandle.close_time <= close_time,
            )
            .order_by(RawCandle.close_time.desc())
            .limit(WINDOW_SIZE)
        )).scalars().all()

        if len(candle_rows) < 200:
            return None

        funding_rows = (await session.execute(
            select(RawFunding)
            .where(
                RawFunding.symbol == symbol,
                RawFunding.settlement_time <= close_time,
                RawFunding.settlement_time >= close_time - timedelta(hours=30),
            )
            .order_by(RawFunding.settlement_time.asc())
        )).scalars().all()

        oi_rows = (await session.execute(
            select(RawOpenInterest)
            .where(
                RawOpenInterest.symbol == symbol,
                RawOpenInterest.captured_at <= close_time,
                RawOpenInterest.captured_at >= close_time - timedelta(hours=26),
            )
            .order_by(RawOpenInterest.captured_at.asc())
        )).scalars().all()

        lsr_rows = (await session.execute(
            select(RawLongShortRatio)
            .where(
                RawLongShortRatio.symbol == symbol,
                RawLongShortRatio.captured_at <= close_time,
            )
            .order_by(RawLongShortRatio.captured_at.desc())
            .limit(1)
        )).scalars().all()

        ob = (await session.execute(
            select(RawOrderbookSnapshot)
            .where(
                RawOrderbookSnapshot.symbol == symbol,
                RawOrderbookSnapshot.captured_at <= close_time,
            )
            .order_by(RawOrderbookSnapshot.captured_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    candles = list(reversed(candle_rows))
    return RawInputs(
        candles=pd.DataFrame([{
            "close_time": r.close_time,
            "open": float(r.open), "high": float(r.high),
            "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
        } for r in candles]),
        funding=pd.DataFrame([{"settlement_time": r.settlement_time, "rate": r.rate} for r in funding_rows]),
        open_interest=pd.DataFrame([{"captured_at": r.captured_at, "open_interest_usd": r.open_interest_usd} for r in oi_rows]),
        long_short_ratio=pd.DataFrame([{"captured_at": r.captured_at, "ratio": r.ratio} for r in lsr_rows]),
        latest_ob_snapshot=(
            {"bids": ob.bids, "asks": ob.asks} if ob is not None else None
        ),
    )


async def _compute_async(symbol: str, timeframe: str, close_time_iso: str) -> None:
    session_factory = get_session_factory()
    close_time = datetime.fromisoformat(close_time_iso)

    inputs = await _load_raw_inputs(session_factory, symbol, timeframe, close_time)
    if inputs is None:
        logger.info("compute_features_insufficient_history",
                    symbol=symbol, tf=timeframe)
        return

    row = build_features_row(symbol, timeframe, close_time, inputs)
    if row is None:
        return

    row["feature_version"] = _FEATURE_VERSION

    async with session_factory() as session:
        update_cols = {k: v for k, v in row.items()
                       if k not in ("symbol", "timeframe", "close_time")}
        stmt = pg_insert(Features).values(**row).on_conflict_do_update(
            index_elements=["symbol", "timeframe", "close_time"],
            set_=update_cols,
        )
        await session.execute(stmt)
        await session.commit()

    FEATURES_COMPUTED.labels(symbol=symbol, timeframe=timeframe).inc()
    logger.info("features_computed", symbol=symbol, tf=timeframe,
                close_time=close_time_iso,
                trend_regime=row["trend_regime"], vol_regime=row["vol_regime"])

    from trading_sandwich.signals.worker import detect_signals as detect_signals_task
    detect_signals_task.apply_async(
        args=[symbol, timeframe, close_time_iso], queue="signals",
    )


@app.task(name="trading_sandwich.features.worker.compute_features")
def compute_features(symbol: str, timeframe: str, close_time_iso: str) -> None:
    with FEATURE_COMPUTE_SECONDS.labels(symbol=symbol, timeframe=timeframe).time():
        run_coro(_compute_async(symbol, timeframe, close_time_iso))
```

- [ ] **Step 5: Run tests — migrations + full-row + previously-green Phase 0 worker test**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_features_full_row.py tests/integration/test_feature_worker.py -v -m integration`
Expected: both PASS. Phase 0's `test_feature_worker.py` still passes because `build_features_row` returns the same-shape dict it used to, just with more keys.

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/features/compute.py src/trading_sandwich/features/worker.py tests/integration/test_features_full_row.py
git commit -m "feat: feature worker computes full 48-column Phase 1 row + regime labels"
```

---

## Task 27: Detector registry + extended Phase 0 `trend_pullback` for Phase 1 regime gating

**Files:**
- Modify: `src/trading_sandwich/signals/detectors/__init__.py` — registry
- Modify: `src/trading_sandwich/signals/detectors/trend_pullback.py` — add regime gate
- Test: extend `tests/unit/test_detector_trend_pullback.py`

Phase 0's `trend_pullback` fires regardless of regime label. Phase 1 tightens the gate: must be `trend_up`/`trend_down` AND `normal`/`expansion`. Detector returns `None` otherwise.

- [ ] **Step 1: Extend trend_pullback tests with regime gate**

Append to `tests/unit/test_detector_trend_pullback.py`:
```python
def test_does_not_fire_when_regime_is_range():
    # Same pattern that fires in test_fires_on_clean_pullback, but with regime
    # inputs forced to `range`
    rows = make_features_series(n=35, close_slope=0.5, rsi_values=[45]*30 + [35]*3 + [42]*2)
    rows[-1] = rows[-1].model_copy(update={
        "close_price": rows[-2].close_price + Decimal("1.5"),
        "rsi_14": Decimal("42"),
        "ema_21": rows[-1].close_price - Decimal("0.5"),
        "trend_regime": "range",
        "vol_regime": "normal",
    })
    rows[-2] = rows[-2].model_copy(update={
        "rsi_14": Decimal("35"), "close_price": rows[-2].ema_21,
        "trend_regime": "range", "vol_regime": "normal",
    })
    rows[-3] = rows[-3].model_copy(update={
        "rsi_14": Decimal("38"),
        "trend_regime": "range", "vol_regime": "normal",
    })
    assert detect_trend_pullback(rows) is None


def test_does_not_fire_when_vol_regime_is_squeeze():
    rows = make_features_series(n=35, close_slope=0.5, rsi_values=[45]*30 + [35]*3 + [42]*2)
    for r_idx in (-1, -2, -3):
        rows[r_idx] = rows[r_idx].model_copy(update={
            "trend_regime": "trend_up", "vol_regime": "squeeze",
        })
    # Also apply the pattern that would otherwise fire
    rows[-1] = rows[-1].model_copy(update={
        "close_price": rows[-2].close_price + Decimal("1.5"),
        "rsi_14": Decimal("42"),
        "ema_21": rows[-1].close_price - Decimal("0.5"),
        "vol_regime": "squeeze",
    })
    rows[-2] = rows[-2].model_copy(update={
        "rsi_14": Decimal("35"), "close_price": rows[-2].ema_21,
        "vol_regime": "squeeze",
    })
    assert detect_trend_pullback(rows) is None
```

Also modify `tests/unit/_fakers.py` so the faker produces rows labeled `trend_up`/`normal` by default (Phase 1 rows always have regimes):
```python
# In tests/unit/_fakers.py, inside make_features_series loop, set:
        rows.append(FeaturesRow(
            symbol=symbol, timeframe=timeframe,
            close_time=start + timedelta(minutes=i),
            close_price=Decimal(str(round(close, 4))),
            ema_21=Decimal(str(round(close + ema_offset, 4))),
            rsi_14=Decimal(str(round(rsi, 2))),
            atr_14=Decimal(str(round(atr, 4))),
            trend_regime="trend_up",
            vol_regime="normal",
            feature_version="test",
        ))
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_trend_pullback.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Add regime gate to `trend_pullback.py`**

In `src/trading_sandwich/signals/detectors/trend_pullback.py`, at the top of `detect_trend_pullback` (after the `MIN_HISTORY` check), add:
```python
    if current.trend_regime not in ("trend_up", "trend_down"):
        return None
    if current.vol_regime not in ("normal", "expansion"):
        return None
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_trend_pullback.py -v`
Expected: all PASS.

- [ ] **Step 5: Create detector registry**

Replace `src/trading_sandwich/signals/detectors/__init__.py` with:
```python
"""Detector registry. Tasks 28-34 add one entry per new detector. The signal
worker iterates this dict on every features close.
"""
from __future__ import annotations

from collections.abc import Callable

from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.signals.detectors.trend_pullback import detect_trend_pullback

DetectorFn = Callable[[list[FeaturesRow]], Signal | None]

REGISTRY: dict[str, DetectorFn] = {
    "trend_pullback": detect_trend_pullback,
}
```

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/signals/detectors/__init__.py src/trading_sandwich/signals/detectors/trend_pullback.py tests/unit/_fakers.py tests/unit/test_detector_trend_pullback.py
git commit -m "feat: detector registry + regime gate on trend_pullback"
```

---

## Task 28: `squeeze_breakout` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/squeeze_breakout.py`
- Create: `tests/unit/test_detector_squeeze_breakout.py`
- Modify: `src/trading_sandwich/signals/detectors/__init__.py` — register

Spec §5.1: fires when `vol_regime` transitions `squeeze` → `expansion` AND close held outside BB for 2 consecutive bars.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_detector_squeeze_breakout.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.squeeze_breakout import detect_squeeze_breakout


def _apply_regime(rows, idx, trend, vol):
    rows[idx] = rows[idx].model_copy(update={"trend_regime": trend, "vol_regime": vol})


def test_fires_on_confirmed_upside_breakout():
    rows = make_features_series(n=30, close_slope=0.2, atr=1.0)
    # Set up: bars 0..27 = squeeze, bar 28 close above bb_upper, bar 29 ALSO above bb_upper
    for i in range(28):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })
    # Transition to expansion, with close > bb_upper on both confirmation bars
    for i, close in ((28, 102), (29, 103)):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "expansion",
            "close_price": Decimal(str(close)),
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })

    s = detect_squeeze_breakout(rows)
    assert s is not None
    assert s.direction == "long"
    assert s.archetype == "squeeze_breakout"


def test_does_not_fire_without_confirmation_bar():
    rows = make_features_series(n=30, close_slope=0.2, atr=1.0)
    for i in range(29):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
        })
    rows[29] = rows[29].model_copy(update={
        "trend_regime": "range", "vol_regime": "expansion",
        "close_price": Decimal("102"),
        "bb_upper": Decimal("100"), "bb_lower": Decimal("99"), "bb_middle": Decimal("99.5"),
    })
    # Only one bar above → no confirmation
    assert detect_squeeze_breakout(rows) is None


def test_fires_on_downside_breakout():
    rows = make_features_series(n=30, close_slope=0.2, atr=1.0)
    for i in range(28):
        _apply_regime(rows, i, "range", "squeeze")
        rows[i] = rows[i].model_copy(update={
            "bb_upper": Decimal("101"), "bb_lower": Decimal("100"), "bb_middle": Decimal("100.5"),
        })
    for i, close in ((28, 98), (29, 97)):
        rows[i] = rows[i].model_copy(update={
            "trend_regime": "range", "vol_regime": "expansion",
            "close_price": Decimal(str(close)),
            "bb_upper": Decimal("101"), "bb_lower": Decimal("100"), "bb_middle": Decimal("100.5"),
        })
    s = detect_squeeze_breakout(rows)
    assert s is not None
    assert s.direction == "short"
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_squeeze_breakout.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/signals/detectors/squeeze_breakout.py`:
```python
"""squeeze_breakout detector.

Fires when:
  - The prior few bars had vol_regime == 'squeeze'.
  - The current bar has vol_regime == 'expansion'.
  - Close has been outside the Bollinger band for the last 2 bars in the same
    direction (confirmation bar).
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 50
SQUEEZE_LOOKBACK = 5   # how far back we require squeeze regime to have been


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_squeeze_breakout(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None

    current = rows[-1]
    prev = rows[-2]

    if current.vol_regime != "expansion":
        return None

    # At least one of the recent bars prior to the breakout must have been in squeeze
    prior_window = rows[-SQUEEZE_LOOKBACK - 2:-2]
    if not any(r.vol_regime == "squeeze" for r in prior_window):
        return None

    # Need BB bounds on both confirmation bars
    if any(getattr(r, attr) is None for r in (current, prev)
           for attr in ("bb_upper", "bb_lower", "atr_14")):
        return None

    direction: str | None = None
    if current.close_price > current.bb_upper and prev.close_price > prev.bb_upper:
        direction = "long"
    elif current.close_price < current.bb_lower and prev.close_price < prev.bb_lower:
        direction = "short"
    if direction is None:
        return None

    # Stop / target: 1.5·ATR stop, 3·ATR target (symmetric)
    atr = current.atr_14
    if direction == "long":
        stop = current.close_price - atr * Decimal("1.5")
        target = current.close_price + atr * Decimal("3.0")
    else:
        stop = current.close_price + atr * Decimal("1.5")
        target = current.close_price - atr * Decimal("3.0")
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    confidence = Decimal("0.8")  # crisp pattern; tighter than divergence/range_rejection

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="squeeze_breakout",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price,
        direction=direction,
        confidence=confidence,
        confidence_breakdown={
            "squeeze_present": 0.4,
            "breakout_direction": 0.3,
            "confirmation_bar": 0.3,
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop,
        target_price=target,
        rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

- [ ] **Step 4: Register detector**

In `src/trading_sandwich/signals/detectors/__init__.py`, add:
```python
from trading_sandwich.signals.detectors.squeeze_breakout import detect_squeeze_breakout

REGISTRY["squeeze_breakout"] = detect_squeeze_breakout
```

- [ ] **Step 5: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_squeeze_breakout.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/signals/detectors/squeeze_breakout.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_squeeze_breakout.py
git commit -m "feat: add squeeze_breakout detector (confirmation-bar gated)"
```

---

## Task 29: `divergence_rsi` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/divergence_rsi.py`
- Create: `tests/unit/test_detector_divergence_rsi.py`
- Modify: `src/trading_sandwich/signals/detectors/__init__.py`

Bullish divergence: price makes a lower low while RSI makes a higher low (between the 2 most recent local lows in an N-bar window). Bearish divergence is the mirror. Phase 1 uses a 20-bar lookback and requires both pivots to have a minimum bar-spacing of 5 bars.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_detector_divergence_rsi.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.divergence_rsi import detect_divergence_rsi


def test_fires_on_bullish_divergence():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    # Configure two price lows: earlier lower low, later higher low; RSI inverse.
    # Force regime = trend_down so bullish divergence (counter-trend long) fires.
    for i, (c, r) in enumerate([
        (100, 50), (99, 48), (95, 32), (94, 28),  # i=0..3 — price down, RSI down
        (96, 36), (97, 40), (98, 44),             # i=4..6 — recovery
        (96, 42), (95, 40), (94, 38),             # i=7..9 — second, lower price low
        (93, 42), (94, 46), (95, 48), (96, 52),   # i=10..13 — RSI higher than before
        (97, 54), (98, 56), (99, 58),
        (100, 60), (101, 62), (102, 64), (103, 66),
        (104, 68), (105, 70), (104, 68), (103, 66),
        (102, 64), (101, 62), (100, 60), (99, 58),
        (98, 56), (97, 54),
    ]):
        rows[i] = rows[i].model_copy(update={
            "close_price": Decimal(str(c)),
            "rsi_14": Decimal(str(r)),
            "trend_regime": "trend_down",
            "vol_regime": "normal",
        })
    # Place the bullish-divergence confirmation on the last bar
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("98"),
        "rsi_14": Decimal("58"),    # higher RSI than the earlier low ~28
        "trend_regime": "trend_down", "vol_regime": "normal",
    })
    s = detect_divergence_rsi(rows)
    assert s is not None
    assert s.direction == "long"
    assert s.archetype == "divergence_rsi"


def test_does_not_fire_in_squeeze():
    rows = make_features_series(n=30, close_slope=-0.3, atr=1.0)
    for r in rows:
        pass  # faker gives trend_up/normal by default
    rows[-1] = rows[-1].model_copy(update={"vol_regime": "squeeze", "trend_regime": "range"})
    assert detect_divergence_rsi(rows) is None
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_divergence_rsi.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/signals/detectors/divergence_rsi.py`:
```python
"""divergence_rsi detector — classic bullish/bearish RSI divergence over a
20-bar lookback. Counter-trend: long in trend_down, short in trend_up.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 40
LOOKBACK = 20
MIN_PIVOT_SPACING = 5


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_divergence_rsi(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None

    current = rows[-1]
    if current.vol_regime not in ("normal", "expansion"):
        return None
    if current.rsi_14 is None or current.atr_14 is None:
        return None

    window = rows[-LOOKBACK:]
    # Find two lowest-price indices at least MIN_PIVOT_SPACING apart
    prices = [(i, float(r.close_price)) for i, r in enumerate(window)]
    rsis = [(i, float(r.rsi_14)) for i, r in enumerate(window) if r.rsi_14 is not None]
    if len(rsis) < 2:
        return None

    # Bullish divergence (long in trend_down): later price low is LOWER than
    # earlier price low, but corresponding RSI is HIGHER.
    # Find the two smallest-price points, ordered by index.
    sorted_by_price = sorted(prices, key=lambda t: t[1])
    cand_bull = _find_divergence_pair(sorted_by_price, window, kind="low",
                                      later_price_lower=True,
                                      later_rsi_higher=True)
    sorted_by_price_desc = sorted(prices, key=lambda t: -t[1])
    cand_bear = _find_divergence_pair(sorted_by_price_desc, window, kind="high",
                                      later_price_lower=False,
                                      later_rsi_higher=False)

    signal: Signal | None = None
    if cand_bull is not None and current.trend_regime == "trend_down":
        signal = _build_signal(current, direction="long", reason=cand_bull)
    elif cand_bear is not None and current.trend_regime == "trend_up":
        signal = _build_signal(current, direction="short", reason=cand_bear)
    return signal


def _find_divergence_pair(sorted_pts, window, kind, later_price_lower, later_rsi_higher):
    for i, (idx_a, price_a) in enumerate(sorted_pts):
        for idx_b, price_b in sorted_pts[i + 1:]:
            earlier, later = (idx_a, idx_b) if idx_a < idx_b else (idx_b, idx_a)
            if later - earlier < MIN_PIVOT_SPACING:
                continue
            p_earlier = float(window[earlier].close_price)
            p_later = float(window[later].close_price)
            r_earlier = float(window[earlier].rsi_14) if window[earlier].rsi_14 is not None else None
            r_later = float(window[later].rsi_14) if window[later].rsi_14 is not None else None
            if r_earlier is None or r_later is None:
                continue
            if later_price_lower and p_later >= p_earlier:
                continue
            if not later_price_lower and p_later <= p_earlier:
                continue
            if later_rsi_higher and r_later <= r_earlier:
                continue
            if not later_rsi_higher and r_later >= r_earlier:
                continue
            # Only signal if the 'later' is close to the most recent bar
            if later < len(window) - 3:
                continue
            return {"earlier": earlier, "later": later,
                    "p_earlier": p_earlier, "p_later": p_later,
                    "r_earlier": r_earlier, "r_later": r_later}
    return None


def _build_signal(current: FeaturesRow, direction: str, reason: dict) -> Signal:
    atr = current.atr_14
    if direction == "long":
        stop = current.close_price - atr * Decimal("1.5")
        target = current.close_price + atr * Decimal("3.0")
    else:
        stop = current.close_price + atr * Decimal("1.5")
        target = current.close_price - atr * Decimal("3.0")
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="divergence_rsi",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price,
        direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "earlier_price": reason["p_earlier"], "later_price": reason["p_later"],
            "earlier_rsi": reason["r_earlier"],   "later_rsi":   reason["r_later"],
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

- [ ] **Step 4: Register**

In `signals/detectors/__init__.py` add:
```python
from trading_sandwich.signals.detectors.divergence_rsi import detect_divergence_rsi
REGISTRY["divergence_rsi"] = detect_divergence_rsi
```

- [ ] **Step 5: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_divergence_rsi.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/signals/detectors/divergence_rsi.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_divergence_rsi.py
git commit -m "feat: add divergence_rsi detector (counter-trend)"
```

---

## Task 30: `divergence_macd` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/divergence_macd.py`
- Create: `tests/unit/test_detector_divergence_macd.py`
- Modify: `signals/detectors/__init__.py`

Same shape as `divergence_rsi` but uses MACD histogram as the oscillator. Since the math is near-duplicate, factor the divergence-finding logic into a shared helper to stay DRY.

- [ ] **Step 1: Extract shared divergence helper**

Create `src/trading_sandwich/signals/detectors/_divergence_core.py`:
```python
"""Shared divergence-pair finder for divergence_rsi + divergence_macd."""
from __future__ import annotations

from trading_sandwich.contracts.models import FeaturesRow

MIN_PIVOT_SPACING = 5


def find_divergence_pair(
    window: list[FeaturesRow],
    *,
    oscillator_attr: str,
    kind: str,                  # "low" (bullish) or "high" (bearish)
) -> dict | None:
    prices = [(i, float(r.close_price)) for i, r in enumerate(window)]
    osc = [(i, float(getattr(r, oscillator_attr)))
           for i, r in enumerate(window)
           if getattr(r, oscillator_attr) is not None]
    if len(osc) < 2:
        return None

    later_price_lower = (kind == "low")
    later_osc_higher = (kind == "low")

    sorted_pts = (
        sorted(prices, key=lambda t: t[1]) if kind == "low"
        else sorted(prices, key=lambda t: -t[1])
    )

    for i, (idx_a, _pa) in enumerate(sorted_pts):
        for idx_b, _pb in sorted_pts[i + 1:]:
            earlier, later = sorted([idx_a, idx_b])
            if later - earlier < MIN_PIVOT_SPACING:
                continue
            p_earlier = float(window[earlier].close_price)
            p_later = float(window[later].close_price)
            r_earlier = getattr(window[earlier], oscillator_attr)
            r_later = getattr(window[later], oscillator_attr)
            if r_earlier is None or r_later is None:
                continue
            r_earlier = float(r_earlier); r_later = float(r_later)
            if later_price_lower and p_later >= p_earlier:
                continue
            if not later_price_lower and p_later <= p_earlier:
                continue
            if later_osc_higher and r_later <= r_earlier:
                continue
            if not later_osc_higher and r_later >= r_earlier:
                continue
            if later < len(window) - 3:
                continue
            return {
                "earlier": earlier, "later": later,
                "p_earlier": p_earlier, "p_later": p_later,
                "osc_earlier": r_earlier, "osc_later": r_later,
            }
    return None
```

- [ ] **Step 2: Refactor `divergence_rsi.py` to use the helper**

Replace the body of `_find_divergence_pair` and the two call sites in `detect_divergence_rsi` with:
```python
from trading_sandwich.signals.detectors._divergence_core import find_divergence_pair

# … inside detect_divergence_rsi, replace the cand_bull / cand_bear calls with:
window = rows[-LOOKBACK:]
cand_bull = find_divergence_pair(window, oscillator_attr="rsi_14", kind="low")
cand_bear = find_divergence_pair(window, oscillator_attr="rsi_14", kind="high")

# And rename the `reason["r_*"]` keys to `reason["osc_*"]` in `_build_signal`
```

- [ ] **Step 3: Run the rsi divergence tests — still green after refactor**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_divergence_rsi.py -v`
Expected: all PASS.

- [ ] **Step 4: Write failing tests for MACD variant**

Create `tests/unit/test_detector_divergence_macd.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.divergence_macd import detect_divergence_macd


def test_fires_on_bearish_macd_divergence_in_uptrend():
    rows = make_features_series(n=30, close_slope=0.3, atr=1.0)
    # Force bearish divergence: price makes higher high, MACD hist makes lower high
    seq = [
        (100, 0.5), (101, 0.6), (103, 0.9), (105, 1.2), (107, 1.5),     # rising
        (106, 1.2), (108, 1.0), (110, 0.8),       # price HH, MACD hist lower
        (109, 0.7), (110.5, 0.65), (111, 0.6),    # confirm on last bar
    ]
    for i, (c, m) in enumerate(seq):
        rows[i] = rows[i].model_copy(update={
            "close_price": Decimal(str(c)),
            "macd_hist": Decimal(str(m)),
            "trend_regime": "trend_up",
            "vol_regime": "normal",
        })
    # Fill bar -1 as the confirmation with price > earlier high, macd hist < earlier high's hist
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("112"), "macd_hist": Decimal("0.5"),
        "trend_regime": "trend_up", "vol_regime": "normal",
    })
    s = detect_divergence_macd(rows)
    assert s is not None
    assert s.direction == "short"
```

- [ ] **Step 5: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_divergence_macd.py -v`
Expected: FAIL.

- [ ] **Step 6: Implement**

Create `src/trading_sandwich/signals/detectors/divergence_macd.py`:
```python
"""divergence_macd detector — same rule shape as divergence_rsi but uses the
MACD histogram as the oscillator.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.signals.detectors._divergence_core import find_divergence_pair

MIN_HISTORY = 40
LOOKBACK = 20


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_divergence_macd(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    current = rows[-1]
    if current.vol_regime not in ("normal", "expansion") or current.atr_14 is None:
        return None
    if current.macd_hist is None:
        return None

    window = rows[-LOOKBACK:]
    cand_bull = find_divergence_pair(window, oscillator_attr="macd_hist", kind="low")
    cand_bear = find_divergence_pair(window, oscillator_attr="macd_hist", kind="high")

    if cand_bull is not None and current.trend_regime == "trend_down":
        return _build(current, "long", cand_bull)
    if cand_bear is not None and current.trend_regime == "trend_up":
        return _build(current, "short", cand_bear)
    return None


def _build(current: FeaturesRow, direction: str, reason: dict) -> Signal:
    atr = current.atr_14
    if direction == "long":
        stop = current.close_price - atr * Decimal("1.5")
        target = current.close_price + atr * Decimal("3.0")
    else:
        stop = current.close_price + atr * Decimal("1.5")
        target = current.close_price - atr * Decimal("3.0")
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="divergence_macd",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price, direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "earlier_price": reason["p_earlier"], "later_price": reason["p_later"],
            "earlier_macd_hist": reason["osc_earlier"], "later_macd_hist": reason["osc_later"],
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

- [ ] **Step 7: Register**

In `signals/detectors/__init__.py`:
```python
from trading_sandwich.signals.detectors.divergence_macd import detect_divergence_macd
REGISTRY["divergence_macd"] = detect_divergence_macd
```

- [ ] **Step 8: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_divergence_macd.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/trading_sandwich/signals/detectors/_divergence_core.py src/trading_sandwich/signals/detectors/divergence_macd.py src/trading_sandwich/signals/detectors/divergence_rsi.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_divergence_macd.py
git commit -m "feat: add divergence_macd detector + refactor shared divergence core"
```

---

## Task 31: `range_rejection` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/range_rejection.py`
- Create: `tests/unit/test_detector_range_rejection.py`
- Modify: `signals/detectors/__init__.py`

Spec §5.1: fires only in `trend_regime==range` AND `vol_regime==normal`. Long on bounce off Donchian-20 lower (wick touches AND closes back inside). Short on rejection at Donchian-20 upper (same rule).

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_detector_range_rejection.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.range_rejection import detect_range_rejection


def test_fires_on_range_low_rejection():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    # Pin Donchian bounds so bar -1 wicks the lower and closes back inside
    for r in rows:
        pass  # faker default
    for i in range(len(rows)):
        rows[i] = rows[i].model_copy(update={
            "donchian_upper": Decimal("110"),
            "donchian_lower": Decimal("95"),
            "trend_regime": "range", "vol_regime": "normal",
        })
    rows[-2] = rows[-2].model_copy(update={"close_price": Decimal("96")})
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("97"),      # closed back inside range
        "swing_low_5": Decimal("94.5"),    # wick touched ≤ 95 earlier
    })
    # The detector reads swing_low_5 as the wick indicator; we've set it to dip below 95.
    s = detect_range_rejection(rows)
    assert s is not None
    assert s.direction == "long"
    assert s.archetype == "range_rejection"


def test_fires_on_range_high_rejection():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    for i in range(len(rows)):
        rows[i] = rows[i].model_copy(update={
            "donchian_upper": Decimal("110"),
            "donchian_lower": Decimal("95"),
            "trend_regime": "range", "vol_regime": "normal",
        })
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("108"),     # closed back inside range
        "swing_high_5": Decimal("110.5"),  # wicked above 110
    })
    s = detect_range_rejection(rows)
    assert s is not None
    assert s.direction == "short"


def test_no_fire_in_trend_regime():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    for i in range(len(rows)):
        rows[i] = rows[i].model_copy(update={
            "donchian_upper": Decimal("110"),
            "donchian_lower": Decimal("95"),
            "trend_regime": "trend_up", "vol_regime": "normal",
        })
    rows[-1] = rows[-1].model_copy(update={
        "close_price": Decimal("97"),
        "swing_low_5": Decimal("94.5"),
    })
    assert detect_range_rejection(rows) is None
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_range_rejection.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/signals/detectors/range_rejection.py`:
```python
"""range_rejection detector.

Fires only in trend_regime=range + vol_regime=normal. Wick-touch-and-close-back
at either Donchian boundary.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 50


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_range_rejection(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None

    current = rows[-1]
    if current.trend_regime != "range" or current.vol_regime != "normal":
        return None
    if any(getattr(current, a) is None
           for a in ("donchian_upper", "donchian_lower", "atr_14", "swing_high_5", "swing_low_5")):
        return None

    direction: str | None = None
    if (
        current.swing_low_5 <= current.donchian_lower
        and current.close_price > current.donchian_lower
    ):
        direction = "long"
    elif (
        current.swing_high_5 >= current.donchian_upper
        and current.close_price < current.donchian_upper
    ):
        direction = "short"

    if direction is None:
        return None

    atr = current.atr_14
    if direction == "long":
        stop = current.swing_low_5 - atr * Decimal("0.5")
        target = current.donchian_upper
    else:
        stop = current.swing_high_5 + atr * Decimal("0.5")
        target = current.donchian_lower
    rr = abs(target - current.close_price) / abs(current.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=current.symbol, timeframe=current.timeframe,
        archetype="range_rejection",
        fired_at=datetime.now(UTC),
        candle_close_time=current.close_time,
        trigger_price=current.close_price, direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "donchian_upper": float(current.donchian_upper),
            "donchian_lower": float(current.donchian_lower),
            "wick_below_low": direction == "long",
            "wick_above_high": direction == "short",
        },
        gating_outcome="below_threshold",
        features_snapshot=current.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

- [ ] **Step 4: Register + run**

In `signals/detectors/__init__.py`:
```python
from trading_sandwich.signals.detectors.range_rejection import detect_range_rejection
REGISTRY["range_rejection"] = detect_range_rejection
```

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_range_rejection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/signals/detectors/range_rejection.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_range_rejection.py
git commit -m "feat: add range_rejection detector"
```

---

## Task 32: `liquidity_sweep_daily` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/liquidity_sweep_daily.py`
- Create: `tests/unit/test_detector_liquidity_sweep_daily.py`
- Modify: `signals/detectors/__init__.py`

Fires when the current bar's high exceeds `prior_day_high` (`swing_high_5` as proxy when high-field isn't on FeaturesRow) AND close is below `prior_day_high` — "swept" prior-day high, direction short. Mirror for prior-day low, direction long. Regime-agnostic.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_detector_liquidity_sweep_daily.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.liquidity_sweep_daily import detect_liquidity_sweep_daily


def test_fires_on_prior_day_high_sweep_short():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    # Last bar swept above prior-day high then closed back below
    rows[-1] = rows[-1].model_copy(update={
        "prior_day_high": Decimal("110"),
        "prior_day_low":  Decimal("100"),
        "swing_high_5":   Decimal("110.5"),
        "swing_low_5":    Decimal("100.5"),
        "close_price":    Decimal("109"),
    })
    s = detect_liquidity_sweep_daily(rows)
    assert s is not None
    assert s.direction == "short"


def test_fires_on_prior_day_low_sweep_long():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "prior_day_high": Decimal("110"),
        "prior_day_low":  Decimal("100"),
        "swing_high_5":   Decimal("109"),
        "swing_low_5":    Decimal("99.5"),
        "close_price":    Decimal("101"),
    })
    s = detect_liquidity_sweep_daily(rows)
    assert s is not None
    assert s.direction == "long"


def test_no_fire_when_close_remains_beyond():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "prior_day_high": Decimal("110"),
        "prior_day_low":  Decimal("100"),
        "swing_high_5":   Decimal("110.5"),
        "swing_low_5":    Decimal("100.5"),
        "close_price":    Decimal("111"),    # stayed above
    })
    assert detect_liquidity_sweep_daily(rows) is None
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/unit/test_detector_liquidity_sweep_daily.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/signals/detectors/liquidity_sweep_daily.py`:
```python
"""liquidity_sweep_daily detector — wick beyond prior-day H or L then close
back inside. Direction is opposite the sweep.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 30


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_liquidity_sweep_daily(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    c = rows[-1]
    if any(getattr(c, a) is None for a in ("prior_day_high", "prior_day_low",
                                           "swing_high_5", "swing_low_5",
                                           "atr_14")):
        return None

    direction: str | None = None
    if c.swing_high_5 > c.prior_day_high and c.close_price < c.prior_day_high:
        direction = "short"
    elif c.swing_low_5 < c.prior_day_low and c.close_price > c.prior_day_low:
        direction = "long"

    if direction is None:
        return None

    atr = c.atr_14
    if direction == "long":
        stop = c.swing_low_5 - atr * Decimal("0.5")
        target = c.close_price + atr * Decimal("2.5")
    else:
        stop = c.swing_high_5 + atr * Decimal("0.5")
        target = c.close_price - atr * Decimal("2.5")
    rr = abs(target - c.close_price) / abs(c.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=c.symbol, timeframe=c.timeframe,
        archetype="liquidity_sweep_daily",
        fired_at=datetime.now(UTC),
        candle_close_time=c.close_time,
        trigger_price=c.close_price, direction=direction,
        confidence=Decimal("0.75"),
        confidence_breakdown={
            "prior_day_high": float(c.prior_day_high),
            "prior_day_low":  float(c.prior_day_low),
            "wick_beyond_and_close_back": True,
        },
        gating_outcome="below_threshold",
        features_snapshot=c.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

- [ ] **Step 4: Register + run + commit**

In registry: `REGISTRY["liquidity_sweep_daily"] = detect_liquidity_sweep_daily`
Run tests; expect PASS.
```bash
git add src/trading_sandwich/signals/detectors/liquidity_sweep_daily.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_liquidity_sweep_daily.py
git commit -m "feat: add liquidity_sweep_daily detector"
```

---

## Task 33: `liquidity_sweep_swing` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/liquidity_sweep_swing.py`
- Create: `tests/unit/test_detector_liquidity_sweep_swing.py`
- Modify: registry

Same rule as Task 32 but references the 20-bar swing H/L (we approximate this in Phase 1 with the MAX(`swing_high_5`) / MIN(`swing_low_5`) across the last 20 rows of `rows`).

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_detector_liquidity_sweep_swing.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.liquidity_sweep_swing import detect_liquidity_sweep_swing


def test_fires_when_wick_beyond_swing_high_and_closes_back():
    rows = make_features_series(n=30, close_slope=0.0, atr=1.0)
    # Build a synthetic 20-bar swing high of 110 (via swing_high_5 peaks)
    for i, sh in enumerate([108, 108, 108, 109, 110, 109, 108, 108, 108, 108,
                            108, 108, 108, 108, 108, 108, 108, 108, 108, 108]):
        rows[-20 + i] = rows[-20 + i].model_copy(update={
            "swing_high_5": Decimal(str(sh)),
            "swing_low_5":  Decimal("98"),
        })
    rows[-1] = rows[-1].model_copy(update={
        "swing_high_5": Decimal("111"),
        "swing_low_5":  Decimal("105"),
        "close_price":  Decimal("108"),
    })
    s = detect_liquidity_sweep_swing(rows)
    assert s is not None
    assert s.direction == "short"
```

- [ ] **Step 2: Run to see fail + implement + register + run + commit**

Create `src/trading_sandwich/signals/detectors/liquidity_sweep_swing.py`:
```python
"""liquidity_sweep_swing — wick beyond trailing 20-bar swing H/L then close back.
Direction opposite the sweep. Regime-agnostic.
"""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 30
SWING_LOOKBACK = 20


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_liquidity_sweep_swing(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    c = rows[-1]
    if c.atr_14 is None or c.swing_high_5 is None or c.swing_low_5 is None:
        return None

    window = rows[-SWING_LOOKBACK - 1:-1]    # 20 bars preceding the current bar
    highs = [r.swing_high_5 for r in window if r.swing_high_5 is not None]
    lows = [r.swing_low_5 for r in window if r.swing_low_5 is not None]
    if not highs or not lows:
        return None
    swing_hi = max(highs)
    swing_lo = min(lows)

    direction: str | None = None
    if c.swing_high_5 > swing_hi and c.close_price < swing_hi:
        direction = "short"
    elif c.swing_low_5 < swing_lo and c.close_price > swing_lo:
        direction = "long"

    if direction is None:
        return None

    atr = c.atr_14
    if direction == "long":
        stop = c.swing_low_5 - atr * Decimal("0.5")
        target = c.close_price + atr * Decimal("2.5")
    else:
        stop = c.swing_high_5 + atr * Decimal("0.5")
        target = c.close_price - atr * Decimal("2.5")
    rr = abs(target - c.close_price) / abs(c.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=c.symbol, timeframe=c.timeframe,
        archetype="liquidity_sweep_swing",
        fired_at=datetime.now(UTC),
        candle_close_time=c.close_time,
        trigger_price=c.close_price, direction=direction,
        confidence=Decimal("0.7"),
        confidence_breakdown={
            "swing_high_20": float(swing_hi),
            "swing_low_20":  float(swing_lo),
        },
        gating_outcome="below_threshold",
        features_snapshot=c.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

In registry: `REGISTRY["liquidity_sweep_swing"] = detect_liquidity_sweep_swing`

Run tests → expect PASS.

```bash
git add src/trading_sandwich/signals/detectors/liquidity_sweep_swing.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_liquidity_sweep_swing.py
git commit -m "feat: add liquidity_sweep_swing detector"
```

---

## Task 34: `funding_extreme` detector

**Files:**
- Create: `src/trading_sandwich/signals/detectors/funding_extreme.py`
- Create: `tests/unit/test_detector_funding_extreme.py`
- Modify: registry

Spec §5.1 + §5.3: counter-funding. Long when `funding_rate < per_symbol long threshold`, short when `> short threshold`. Gate: `vol_regime ∈ {normal, expansion}`.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_detector_funding_extreme.py`:
```python
from decimal import Decimal

from tests.unit._fakers import make_features_series
from trading_sandwich.signals.detectors.funding_extreme import detect_funding_extreme


def test_long_when_funding_below_threshold():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "BTCUSDT",
        "funding_rate": Decimal("-0.0010"),  # below the -0.0003 BTC threshold
        "vol_regime": "normal",
    })
    s = detect_funding_extreme(rows)
    assert s is not None
    assert s.direction == "long"


def test_short_when_funding_above_threshold():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "BTCUSDT",
        "funding_rate": Decimal("0.0010"),   # above 0.0003 threshold
        "vol_regime": "normal",
    })
    s = detect_funding_extreme(rows)
    assert s is not None
    assert s.direction == "short"


def test_uses_default_threshold_for_unknown_symbol():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "NEWCOIN",
        "funding_rate": Decimal("-0.0010"),  # below -0.0005 default
        "vol_regime": "normal",
    })
    s = detect_funding_extreme(rows)
    assert s is not None
    assert s.direction == "long"


def test_no_fire_when_vol_is_squeeze():
    rows = make_features_series(n=10, atr=1.0)
    rows[-1] = rows[-1].model_copy(update={
        "symbol": "BTCUSDT",
        "funding_rate": Decimal("0.0010"),
        "vol_regime": "squeeze",
    })
    assert detect_funding_extreme(rows) is None
```

- [ ] **Step 2: Run → fail → implement**

Create `src/trading_sandwich/signals/detectors/funding_extreme.py`:
```python
"""funding_extreme detector. Counter-funding."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from trading_sandwich._policy import get_funding_threshold
from trading_sandwich.contracts.models import FeaturesRow, Signal

MIN_HISTORY = 3


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()[:12]
    except Exception:
        return "unknown"


_DETECTOR_VERSION = _git_sha()


def detect_funding_extreme(rows: list[FeaturesRow]) -> Signal | None:
    if len(rows) < MIN_HISTORY:
        return None
    c = rows[-1]
    if c.vol_regime not in ("normal", "expansion"):
        return None
    if c.funding_rate is None or c.atr_14 is None:
        return None

    long_thr, short_thr = get_funding_threshold(c.symbol)
    direction: str | None = None
    if c.funding_rate <= long_thr:
        direction = "long"
    elif c.funding_rate >= short_thr:
        direction = "short"
    if direction is None:
        return None

    atr = c.atr_14
    if direction == "long":
        stop = c.close_price - atr * Decimal("1.5")
        target = c.close_price + atr * Decimal("3.0")
    else:
        stop = c.close_price + atr * Decimal("1.5")
        target = c.close_price - atr * Decimal("3.0")
    rr = abs(target - c.close_price) / abs(c.close_price - stop)

    return Signal(
        signal_id=uuid4(),
        symbol=c.symbol, timeframe=c.timeframe,
        archetype="funding_extreme",
        fired_at=datetime.now(UTC),
        candle_close_time=c.close_time,
        trigger_price=c.close_price, direction=direction,
        confidence=Decimal("0.72"),
        confidence_breakdown={
            "funding_rate": float(c.funding_rate),
            "threshold_long":  float(long_thr),
            "threshold_short": float(short_thr),
        },
        gating_outcome="below_threshold",
        features_snapshot=c.model_dump(mode="json"),
        stop_price=stop, target_price=target, rr_ratio=rr,
        detector_version=_DETECTOR_VERSION,
    )
```

In registry: `REGISTRY["funding_extreme"] = detect_funding_extreme`

Run tests → PASS.

```bash
git add src/trading_sandwich/signals/detectors/funding_extreme.py src/trading_sandwich/signals/detectors/__init__.py tests/unit/test_detector_funding_extreme.py
git commit -m "feat: add funding_extreme detector (per-symbol thresholds)"
```

---

# Checkpoint H — pause for human review

Tasks 26–34 complete. Feature worker now writes all 48 Phase 1 columns + regime labels. All 8 detectors exist and are registered. Each detector has dedicated unit tests. Regime gates work as specified.

**Before continuing to Checkpoint I, verify:**
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm tools ruff check src tests
MSYS_NO_PATHCONV=1 docker compose run --rm test -q
```
All tests green; suite should be growing steadily (~70 unit tests + ~8 integration).

---

## Task 35: Dedup gate implementation

**Files:**
- Create: `src/trading_sandwich/signals/dedup.py`
- Test: `tests/integration/test_dedup_gate.py`

Dedup is a Postgres lookup: "is there a claude_triaged signal for (symbol, direction) on a higher timeframe within the last dedup_window_minutes?"

- [ ] **Step 1: Write failing integration test**

Create `tests/integration/test_dedup_gate.py`:
```python
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _insert_signal(async_url: str, *, symbol, timeframe, direction, gating_outcome, fired_at):
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO signals (signal_id,symbol,timeframe,archetype,"
                    "fired_at,candle_close_time,trigger_price,direction,confidence,"
                    "confidence_breakdown,gating_outcome,features_snapshot,detector_version) "
                    "VALUES (:id,:s,:tf,:a,:f,:f,100,:d,0.8,CAST('{}' AS jsonb),:go,"
                    "CAST('{}' AS jsonb),'test')"
                ), {"id": uuid4(), "s": symbol, "tf": timeframe,
                    "a": "trend_pullback", "f": fired_at,
                    "d": direction, "go": gating_outcome})
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_dedup_suppresses_5m_when_higher_tf_recent(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        _insert_signal(url, symbol="BTCUSDT", timeframe="1h", direction="long",
                       gating_outcome="claude_triaged", fired_at=now - timedelta(minutes=10))

        from trading_sandwich.signals.dedup import is_dedup_suppressed
        suppressed = is_dedup_suppressed(
            symbol="BTCUSDT", direction="long", timeframe="5m",
            fired_at=now, window_minutes=30,
        )
        assert suppressed is True


@pytest.mark.integration
def test_dedup_does_not_suppress_same_tf(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        _insert_signal(url, symbol="BTCUSDT", timeframe="5m", direction="long",
                       gating_outcome="claude_triaged", fired_at=now - timedelta(minutes=10))

        from trading_sandwich.signals.dedup import is_dedup_suppressed
        suppressed = is_dedup_suppressed(
            symbol="BTCUSDT", direction="long", timeframe="5m",
            fired_at=now, window_minutes=30,
        )
        assert suppressed is False


@pytest.mark.integration
def test_dedup_does_not_suppress_opposite_direction(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")

        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        _insert_signal(url, symbol="BTCUSDT", timeframe="1h", direction="short",
                       gating_outcome="claude_triaged", fired_at=now - timedelta(minutes=10))

        from trading_sandwich.signals.dedup import is_dedup_suppressed
        assert not is_dedup_suppressed(
            symbol="BTCUSDT", direction="long", timeframe="5m",
            fired_at=now, window_minutes=30,
        )
```

- [ ] **Step 2: Run to see fail**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_dedup_gate.py -v -m integration`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `src/trading_sandwich/signals/dedup.py`:
```python
"""Dedup gate: strictly-higher-timeframe signal for the same (symbol, direction)
within the dedup window suppresses the current candidate.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from trading_sandwich._async import run_coro
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM

_TIMEFRAME_RANK = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "1d": 4}


def _higher_timeframes(timeframe: str) -> list[str]:
    rank = _TIMEFRAME_RANK.get(timeframe, -1)
    return [tf for tf, r in _TIMEFRAME_RANK.items() if r > rank]


async def _check_async(
    symbol: str, direction: str, timeframe: str,
    fired_at: datetime, window_minutes: int,
) -> bool:
    session_factory = get_session_factory()
    higher = _higher_timeframes(timeframe)
    if not higher:
        return False
    cutoff = fired_at - timedelta(minutes=window_minutes)
    async with session_factory() as session:
        hit = (await session.execute(
            select(SignalORM.signal_id)
            .where(
                SignalORM.symbol == symbol,
                SignalORM.direction == direction,
                SignalORM.gating_outcome == "claude_triaged",
                SignalORM.timeframe.in_(higher),
                SignalORM.fired_at >= cutoff,
                SignalORM.fired_at <= fired_at,
            )
            .limit(1)
        )).scalar_one_or_none()
    return hit is not None


def is_dedup_suppressed(
    *,
    symbol: str, direction: str, timeframe: str,
    fired_at: datetime, window_minutes: int,
) -> bool:
    return run_coro(_check_async(symbol, direction, timeframe, fired_at, window_minutes))
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_dedup_gate.py -v -m integration`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/signals/dedup.py tests/integration/test_dedup_gate.py
git commit -m "feat: add dedup gate (strictly-higher-TF claude_triaged lookup)"
```

---

## Task 36: Signal worker — iterate registry + three-stage gating

**Files:**
- Modify: `src/trading_sandwich/signals/worker.py`
- Modify: `src/trading_sandwich/signals/gating.py` — plug dedup stage
- Test: rewrite `tests/integration/test_signal_worker.py`

Phase 0's signal worker ran only `detect_trend_pullback` and applied threshold+cooldown. Phase 1 iterates every entry in `REGISTRY`, applies threshold → cooldown → dedup in order, persists each result.

- [ ] **Step 1: Extend `gating.py` with a composite `gate_signal` entry point**

Replace `src/trading_sandwich/signals/gating.py` body (keep the Phase-0 in-memory GatingState helpers for back-compat/unit tests but add a Phase-1 real gate):

```python
"""Phase 0 in-memory gating (threshold + cooldown) — retained for unit tests.
Phase 1 adds `gate_signal_with_db`, the three-stage gate used by the signal
worker against Postgres.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from trading_sandwich._async import run_coro
from trading_sandwich._policy import (
    get_confidence_threshold,
    get_cooldown_minutes,
    get_dedup_window_minutes,
)
from trading_sandwich.contracts.models import Signal
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.signals.dedup import is_dedup_suppressed


@dataclass
class GatingState:
    last_fired: dict[tuple[str, str], datetime] = field(default_factory=dict)


def apply_gating(signal: Signal, state: GatingState, policy: dict) -> Signal:
    threshold = Decimal(str(policy["per_archetype_confidence_threshold"][signal.archetype]))
    if signal.confidence < threshold:
        return signal.model_copy(update={"gating_outcome": "below_threshold"})

    cooldown_min = policy["per_archetype_cooldown_minutes"][signal.archetype]
    key = (signal.symbol, signal.archetype)
    last = state.last_fired.get(key)
    if last is not None and signal.fired_at - last < timedelta(minutes=cooldown_min):
        return signal.model_copy(update={"gating_outcome": "cooldown_suppressed"})

    state.last_fired[key] = signal.fired_at
    return signal.model_copy(update={"gating_outcome": "claude_triaged"})


async def _cooldown_violated_async(signal: Signal) -> bool:
    cooldown_min = get_cooldown_minutes(signal.archetype)
    cutoff = signal.fired_at - timedelta(minutes=cooldown_min)
    session_factory = get_session_factory()
    async with session_factory() as session:
        last = (await session.execute(
            select(SignalORM.fired_at)
            .where(
                SignalORM.symbol == signal.symbol,
                SignalORM.archetype == signal.archetype,
                SignalORM.gating_outcome == "claude_triaged",
                SignalORM.fired_at >= cutoff,
                SignalORM.fired_at <= signal.fired_at,
            )
            .order_by(SignalORM.fired_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    return last is not None


def gate_signal_with_db(signal: Signal) -> Signal:
    """Three-stage gate applied strictly in order:
       1. below_threshold
       2. cooldown_suppressed
       3. dedup_suppressed
    First non-pass stage short-circuits.
    """
    # Stage 1 — threshold
    threshold = get_confidence_threshold(signal.archetype)
    if signal.confidence < threshold:
        return signal.model_copy(update={"gating_outcome": "below_threshold"})

    # Stage 2 — cooldown
    if run_coro(_cooldown_violated_async(signal)):
        return signal.model_copy(update={"gating_outcome": "cooldown_suppressed"})

    # Stage 3 — dedup
    window = get_dedup_window_minutes()
    if is_dedup_suppressed(
        symbol=signal.symbol, direction=signal.direction,
        timeframe=signal.timeframe, fired_at=signal.fired_at,
        window_minutes=window,
    ):
        return signal.model_copy(update={"gating_outcome": "dedup_suppressed"})

    return signal.model_copy(update={"gating_outcome": "claude_triaged"})
```

- [ ] **Step 2: Rewrite the signal worker to iterate REGISTRY**

Replace the body of `src/trading_sandwich/signals/worker.py`:
```python
"""Signal worker. Celery consumer that reads features context, iterates the
detector registry, applies three-stage gating, persists results, and schedules
outcome measurement for claude_triaged signals.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Features as FeaturesORM
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.logging import get_logger
from trading_sandwich.metrics import SIGNALS_FIRED
from trading_sandwich.signals.detectors import REGISTRY
from trading_sandwich.signals.gating import gate_signal_with_db

logger = get_logger(__name__)

LOOKBACK = 60
HORIZONS_SECONDS: dict[str, int] = {
    "15m": 15 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60,
    "24h": 24 * 60 * 60, "3d": 3 * 24 * 60 * 60, "7d": 7 * 24 * 60 * 60,
}


def _row_to_features(r: FeaturesORM) -> FeaturesRow:
    return FeaturesRow(
        symbol=r.symbol, timeframe=r.timeframe, close_time=r.close_time,
        close_price=r.close_price,
        ema_8=r.ema_8, ema_21=r.ema_21, ema_55=r.ema_55, ema_200=r.ema_200,
        rsi_14=r.rsi_14, atr_14=r.atr_14,
        macd_line=r.macd_line, macd_signal=r.macd_signal, macd_hist=r.macd_hist,
        adx_14=r.adx_14, di_plus_14=r.di_plus_14, di_minus_14=r.di_minus_14,
        stoch_rsi_k=r.stoch_rsi_k, stoch_rsi_d=r.stoch_rsi_d, roc_10=r.roc_10,
        bb_upper=r.bb_upper, bb_middle=r.bb_middle, bb_lower=r.bb_lower, bb_width=r.bb_width,
        keltner_upper=r.keltner_upper, keltner_middle=r.keltner_middle, keltner_lower=r.keltner_lower,
        donchian_upper=r.donchian_upper, donchian_middle=r.donchian_middle, donchian_lower=r.donchian_lower,
        obv=r.obv, vwap=r.vwap, volume_zscore_20=r.volume_zscore_20, mfi_14=r.mfi_14,
        swing_high_5=r.swing_high_5, swing_low_5=r.swing_low_5,
        pivot_p=r.pivot_p, pivot_r1=r.pivot_r1, pivot_r2=r.pivot_r2,
        pivot_s1=r.pivot_s1, pivot_s2=r.pivot_s2,
        prior_day_high=r.prior_day_high, prior_day_low=r.prior_day_low,
        prior_week_high=r.prior_week_high, prior_week_low=r.prior_week_low,
        funding_rate=r.funding_rate, funding_rate_24h_mean=r.funding_rate_24h_mean,
        open_interest_usd=r.open_interest_usd,
        oi_delta_1h=r.oi_delta_1h, oi_delta_24h=r.oi_delta_24h,
        long_short_ratio=r.long_short_ratio, ob_imbalance_05=r.ob_imbalance_05,
        ema_21_slope_bps=r.ema_21_slope_bps,
        atr_percentile_100=r.atr_percentile_100,
        bb_width_percentile_100=r.bb_width_percentile_100,
        trend_regime=r.trend_regime, vol_regime=r.vol_regime,
        feature_version=r.feature_version,
    )


async def _load_features(symbol: str, timeframe: str, close_time: datetime) -> list[FeaturesRow]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        orm_rows = (await session.execute(
            select(FeaturesORM)
            .where(
                FeaturesORM.symbol == symbol,
                FeaturesORM.timeframe == timeframe,
                FeaturesORM.close_time <= close_time,
            )
            .order_by(FeaturesORM.close_time.desc())
            .limit(LOOKBACK)
        )).scalars().all()
    return [_row_to_features(r) for r in reversed(orm_rows)]


async def _persist_signal(signal: Signal) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = pg_insert(SignalORM).values(
            signal_id=signal.signal_id, symbol=signal.symbol, timeframe=signal.timeframe,
            archetype=signal.archetype, fired_at=signal.fired_at,
            candle_close_time=signal.candle_close_time,
            trigger_price=signal.trigger_price, direction=signal.direction,
            confidence=signal.confidence, confidence_breakdown=signal.confidence_breakdown,
            gating_outcome=signal.gating_outcome,
            features_snapshot=signal.features_snapshot,
            stop_price=signal.stop_price, target_price=signal.target_price, rr_ratio=signal.rr_ratio,
            detector_version=signal.detector_version,
        ).on_conflict_do_nothing(index_elements=["signal_id"])
        await session.execute(stmt)
        await session.commit()


def _schedule_outcomes(signal: Signal) -> None:
    from trading_sandwich.outcomes.worker import measure_outcome as measure_outcome_task
    for horizon, secs in HORIZONS_SECONDS.items():
        measure_outcome_task.apply_async(
            args=[str(signal.signal_id), horizon],
            queue="outcomes",
            countdown=secs,
        )


async def _detect_async(symbol: str, timeframe: str, close_time_iso: str) -> None:
    close_time = datetime.fromisoformat(close_time_iso)
    features = await _load_features(symbol, timeframe, close_time)
    if not features:
        return

    for archetype, detector_fn in REGISTRY.items():
        try:
            sig = detector_fn(features)
        except Exception as exc:
            logger.exception("detector_error", archetype=archetype, err=str(exc))
            continue
        if sig is None:
            continue

        gated = gate_signal_with_db(sig)
        await _persist_signal(gated)
        SIGNALS_FIRED.labels(
            symbol=sig.symbol, timeframe=sig.timeframe,
            archetype=sig.archetype, gating_outcome=gated.gating_outcome,
        ).inc()
        if gated.gating_outcome == "claude_triaged":
            _schedule_outcomes(gated)


@app.task(name="trading_sandwich.signals.worker.detect_signals")
def detect_signals(symbol: str, timeframe: str, close_time_iso: str) -> None:
    run_coro(_detect_async(symbol, timeframe, close_time_iso))
```

- [ ] **Step 3: Update `tests/integration/test_signal_worker.py`**

Since this integration test was written in Phase 0 for a single-detector worker, update its assertions for Phase 1. Replace the Phase 0 test body's assertion block with:
```python
        rows = _signals_rows(pg_url)
        # Phase 1 iterates all detectors; the seeded pattern may match multiple
        # archetypes. Assert at least one trend_pullback row persisted with
        # claude_triaged gating.
        archetype_to_outcome = {r["archetype"]: r["gating_outcome"] for r in rows}
        assert "trend_pullback" in archetype_to_outcome
        assert archetype_to_outcome["trend_pullback"] == "claude_triaged"

        messages = _drain_outcomes_queue(redis_url)
        # All 6 horizons now, not 2
        assert len(messages) == 6
        horizons = {m["args"][1] for m in messages}
        assert horizons == {"15m", "1h", "4h", "24h", "3d", "7d"}
```

Also update `_seed_features` in that test file to include the Phase 1 columns the detectors expect on the last-bar (`trend_regime`, `vol_regime`, and any Donchian/Bollinger fields referenced by the other detectors). For simplicity, set `trend_regime='trend_up'`, `vol_regime='normal'`, and leave the others NULL — the other detectors skip gracefully on NULL inputs, so only trend_pullback fires, matching the assertion above.

Add to the seed query:
```python
"INSERT INTO features (symbol,timeframe,close_time,close_price,ema_21,rsi_14,atr_14,"
"feature_version,trend_regime,vol_regime) "
"VALUES (:s,:t,:ct,:cp,:e,:r,:a,:v,'trend_up','normal')"
```

- [ ] **Step 4: Run tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_signal_worker.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/signals/worker.py src/trading_sandwich/signals/gating.py tests/integration/test_signal_worker.py
git commit -m "feat: signal worker iterates full registry with three-stage gating"
```

---

## Task 37: Outcome worker — schedule all 6 horizons + redbeat persistence

**Files:**
- Modify: `src/trading_sandwich/outcomes/worker.py` — ensure 6 horizons, no other change needed
- Test: `tests/integration/test_outcome_horizons_all.py`

Phase 0's outcome worker already accepts any horizon from `HORIZON_MINUTES` (which had all 6 defined). The scheduling of all 6 now happens in Task 36's signal worker. This task adds an integration test that verifies the countdown-scheduled tasks are durably queued in Redis (i.e., redbeat / Celery survive a broker "restart" by re-fetching scheduled tasks).

- [ ] **Step 1: Write failing integration test**

Create `tests/integration/test_outcome_horizons_all.py`:
```python
import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


def _seed_signal_and_forward_candles(async_url: str) -> tuple[datetime, str]:
    base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    signal_id = uuid4()
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO signals (signal_id,symbol,timeframe,archetype,fired_at,"
                    "candle_close_time,trigger_price,direction,confidence,confidence_breakdown,"
                    "gating_outcome,features_snapshot,stop_price,target_price,rr_ratio,detector_version)"
                    " VALUES (:id,:s,:t,:a,:f,:cct,:tp,:d,:c,CAST('{}' AS jsonb),"
                    ":go,CAST(:snap AS jsonb),:sp,:tg,:rr,:dv)"
                ), {
                    "id": signal_id, "s": "BTCUSDT", "t": "5m", "a": "trend_pullback",
                    "f": base, "cct": base,
                    "tp": Decimal("100"), "d": "long", "c": Decimal("0.9"),
                    "go": "claude_triaged",
                    "snap": '{"atr_14": "1.0"}',
                    "sp": Decimal("99"), "tg": Decimal("102"),
                    "rr": Decimal("2"), "dv": "test",
                })
                for i in range(2016):   # 7 days × 24h × 12 5m-bars/hr
                    ot = base + timedelta(minutes=5 * i)
                    ct = ot + timedelta(minutes=5)
                    close = 100 + min(i * 0.01, 20)
                    await conn.execute(text(
                        "INSERT INTO raw_candles (symbol,timeframe,open_time,close_time,open,high,low,close,volume) "
                        "VALUES (:s,:t,:ot,:ct,:o,:h,:l,:c,10)"
                    ), {"s": "BTCUSDT", "t": "5m", "ot": ot, "ct": ct,
                        "o": close - 0.1, "h": close + 0.3, "l": close - 0.3, "c": close})
        finally:
            await engine.dispose()
    asyncio.run(_run())
    return base, str(signal_id)


def _outcome_rows(async_url: str, signal_id: str) -> list[dict]:
    async def _run() -> list[dict]:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT horizon FROM signal_outcomes WHERE signal_id=:id"
                ), {"id": signal_id})).all()
                return [r.horizon for r in rows]
        finally:
            await engine.dispose()
    return asyncio.run(_run())


@pytest.mark.integration
@pytest.mark.timeout(120)
def test_measure_outcome_writes_all_6_horizons(env_for_postgres, env_for_redis):
    with (
        PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as rd,
    ):
        pg_url = pg.get_connection_url()
        redis_url = f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0"
        env_for_redis(redis_url)
        env_for_postgres(pg_url)
        command.upgrade(Config("alembic.ini"), "head")

        _, signal_id = _seed_signal_and_forward_candles(pg_url)
        from trading_sandwich.outcomes.worker import measure_outcome
        for horizon in ("15m", "1h", "4h", "24h", "3d", "7d"):
            measure_outcome.run(signal_id, horizon)
        horizons = sorted(_outcome_rows(pg_url, signal_id))
        assert set(horizons) == {"15m", "1h", "4h", "24h", "3d", "7d"}
```

- [ ] **Step 2: Run**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm test tests/integration/test_outcome_horizons_all.py -v -m integration`
Expected: PASS (HORIZON_MINUTES already covers all 6 horizons in Phase 0 code).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_outcome_horizons_all.py
git commit -m "test: integration test that all 6 outcome horizons can be measured"
```

---

# Checkpoint I — pause for human review

Tasks 35–37 complete. Dedup gate works in Postgres. Signal worker iterates the full detector registry and applies three-stage gating. Outcome worker already supports all 6 horizons; integration test confirms it. Suite stays green.

**Verify:**
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm tools ruff check src tests
MSYS_NO_PATHCONV=1 docker compose run --rm test -q
```

---

*(Plan continues in Part 4: Tasks 38–56 cover backfill tooling — REST raw candles, REST microstructure, features backfill — plus metrics port allocator, observability, E2E test, deploy runbook, self-review, execution handoff.)*
