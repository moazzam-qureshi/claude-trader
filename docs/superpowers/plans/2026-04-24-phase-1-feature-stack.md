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

*(Plan continues with Tasks 11–55 in subsequent commits. This document grows as tasks are drafted. Next commit adds Tasks 11–25 covering migration 0008 archetype-check, 0009 raw_candles partitioning, `policy.yaml` loader helpers, Binance REST poller infrastructure + unit tests, Binance depth-stream ingestor + integration test, and microstructure raw-table ORM coverage.)*
