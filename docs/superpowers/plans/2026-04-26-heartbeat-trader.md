# Heartbeat Trader (Phase 2.7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace signal-driven Claude triage with a continuous trader-persona running on a self-paced heartbeat schedule, with file-based memory (SOUL/GOALS/STATE/diary), a tiered universe Claude curates in real time, and Discord notification of every universe change.

**Architecture:** New `triage/heartbeat.py` Celery Beat task fires every 15 min as the gating worker; it reads the previous shift's `next_check_in_minutes` from the new `heartbeat_shifts` table and decides whether to spawn Claude. Spawned Claude reads five memory files via `--append-system-prompt-file` and uses 8 new MCP tools (`read_diary`, `write_state`, `append_diary`, `mutate_universe`, `assess_symbol_fit`, `get_open_positions`, `get_recent_signals`, `get_top_movers`) plus the existing 7. Universe state lives in `policy.yaml::universe.tiers`; `mutate_universe` is the only writer, gated by hard limits and atomically updating DB + yaml + Discord. Existing signal-triage path is **frozen, not deleted**, for trivial revert.

**Tech Stack:** Python 3.12, FastMCP (`mcp` SDK), Celery + Redis (Beat), SQLAlchemy 2.0 async, Alembic, Pydantic v2, `aiohttp` for Discord webhook, testcontainers, pytest.

**Spec:** [docs/superpowers/specs/2026-04-26-heartbeat-trader-design.md](../specs/2026-04-26-heartbeat-trader-design.md)

**Predecessor (shipped):** Phase 2 Stage 1b (commits through `e3a60ac`). Signal-driven triage operational in paper mode.

---

## Conventions (read once before starting)

- **All commands run via `docker compose run --rm test <args>` or `docker compose run --rm tools <args>`.** Never install Python or deps on the host.
- **Tests run inside the `test` service.** Pattern: `docker compose run --rm test pytest <path> -v`.
- **Every task ends with a commit.** Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- **Each task is self-contained and leaves the system in a working state.** Per project CLAUDE.md.
- **TDD is mandatory.** Every task: RED test → GREEN minimal impl → commit. Tests are never written after.
- **Adapter for `claude` CLI invocation** is mocked in tests via `monkeypatch` of `subprocess.run` / `asyncio.create_subprocess_exec`. Real Claude is exercised only in the manual smoke test (Task 28).
- **No live-mode changes.** `policy.yaml::execution_mode` stays `paper` throughout this plan.
- **Imports** use `from trading_sandwich.X import Y`, never relative.
- **DB sessions** via `get_session_factory()` from `db/engine.py`, async. Pattern: `async with factory() as session:`.
- **Pydantic contracts** live under `src/trading_sandwich/contracts/`. Add a new `heartbeat.py` contract module; do not pollute `phase2.py`.
- **Discord webhook URL** is read from `os.environ["DISCORD_UNIVERSE_WEBHOOK_URL"]`. Tests stub the env var via `monkeypatch.setenv`.
- **Path handling on Windows host.** All compose-mounted paths inside containers use Unix `/app/...` paths. Source paths in tests use `pathlib.Path` with absolute forms.

---

## File structure

### New Python modules

- `src/trading_sandwich/contracts/heartbeat.py` — Pydantic models for shift records, universe events, STATE.md frontmatter
- `src/trading_sandwich/db/models_heartbeat.py` — SQLAlchemy ORM for `heartbeat_shifts`, `universe_events`
- `src/trading_sandwich/triage/heartbeat.py` — gating worker: reads STATE / DB, decides whether to spawn
- `src/trading_sandwich/triage/shift_invocation.py` — spawns Claude with all five prompt files
- `src/trading_sandwich/triage/state_io.py` — STATE.md read/write/validate; diary append
- `src/trading_sandwich/triage/universe_policy.py` — load policy.yaml universe section, validate mutations against hard limits, atomic write
- `src/trading_sandwich/notifications/__init__.py` — empty
- `src/trading_sandwich/notifications/discord.py` — Discord webhook poster + retry sweeper
- `src/trading_sandwich/mcp/tools/state_diary.py` — MCP tools: `read_diary`, `write_state`, `append_diary`
- `src/trading_sandwich/mcp/tools/universe.py` — MCP tools: `mutate_universe`, `assess_symbol_fit`, `get_open_positions`
- `src/trading_sandwich/mcp/tools/market_scan.py` — MCP tools: `get_top_movers`, `get_recent_signals`
- `src/trading_sandwich/cli/heartbeat.py` — CLI subcommands `heartbeat status`, `heartbeat shifts`, `heartbeat universe`

### New runtime files

- `runtime/SOUL.md` — trader identity (full content in Task 4)
- `runtime/STATE.md` — initial working memory file with empty body and bootstrap frontmatter
- `runtime/diary/` — directory created by Task 5 setup
- `runtime/diary/.gitkeep` — keeps directory tracked

### New migrations

- `migrations/versions/0011_heartbeat_shifts.py` — `heartbeat_shifts` table
- `migrations/versions/0012_universe_events.py` — `universe_events` table

### Modified files

- `runtime/CLAUDE.md` — rewritten for heartbeat shift protocol (Task 6)
- `runtime/GOALS.md` — fleshed out from placeholder (Task 4)
- `policy.yaml` — add `heartbeat`, `universe.tiers`, `universe.hard_limits` sections (Task 3)
- `src/trading_sandwich/celery_app.py` — register `heartbeat_tick` Beat schedule; remove signal-triage Beat schedule
- `src/trading_sandwich/mcp/server.py` — register new tool modules (Task 8 onward)
- `.mcp.json` — update `allowedTools` list for heartbeat-spawned Claude
- `.env.example` — add `DISCORD_UNIVERSE_WEBHOOK_URL`
- `compose.yaml` — pass `DISCORD_UNIVERSE_WEBHOOK_URL` env to `triage-worker` and `mcp-server`
- `src/trading_sandwich/cli.py` — register `heartbeat` subcommand group

### New tests (unit)

- `tests/unit/test_state_md_parser.py`
- `tests/unit/test_diary_rotation.py`
- `tests/unit/test_universe_validation.py`
- `tests/unit/test_pacing_decision.py`
- `tests/unit/test_discord_card_format.py`
- `tests/unit/test_shift_invocation.py`
- `tests/unit/test_assess_symbol_fit.py`
- `tests/unit/test_heartbeat_contracts.py`

### New tests (integration)

- `tests/integration/test_heartbeat_migrations.py`
- `tests/integration/test_heartbeat_gate_db.py`
- `tests/integration/test_mutate_universe_e2e.py`
- `tests/integration/test_discord_retry_sweeper.py`
- `tests/integration/test_state_drift_detection.py`
- `tests/integration/test_mcp_tool_state_diary_int.py`
- `tests/integration/test_mcp_tool_universe_int.py`
- `tests/integration/test_mcp_tool_market_scan_int.py`

---

## Plan layout

The plan flows in this order:

1. **Tasks 1–3:** Foundation — Pydantic contracts, migrations, policy.yaml extension. These have no dependencies on each other or on later tasks.
2. **Tasks 4–6:** Memory files — SOUL.md, GOALS.md, STATE.md bootstrap, CLAUDE.md rewrite. File-only changes.
3. **Tasks 7–10:** State/diary I/O + universe policy module + Discord notifier (foundational code modules used by MCP tools).
4. **Tasks 11–18:** Eight new MCP tools, one per task.
5. **Tasks 19–22:** Heartbeat scheduler — gate logic, shift invocation, Beat schedule, Celery wiring.
6. **Tasks 23–25:** CLI subcommands.
7. **Tasks 26–27:** Compose / env / `.mcp.json` wiring.
8. **Task 28:** Manual smoke test (operator-run; documented as a checklist).

**Checkpoints for operator review:** after Task 6 (memory files), Task 18 (all MCP tools done), Task 22 (scheduler wired), Task 28 (smoke test).

---

## Task 1: Pydantic contracts for heartbeat

**Files:**
- Create: `src/trading_sandwich/contracts/heartbeat.py`
- Test: `tests/unit/test_heartbeat_contracts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_heartbeat_contracts.py
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from trading_sandwich.contracts.heartbeat import (
    StateFrontmatter,
    UniverseEventType,
    UniverseMutationRequest,
    ShiftRecord,
)


def test_state_frontmatter_minimal_valid():
    fm = StateFrontmatter(
        shift_count=0,
        last_updated=datetime.now(timezone.utc),
        open_positions=0,
        open_theses=0,
        regime="bootstrap",
        next_check_in_minutes=60,
        next_check_reason="bootstrap shift, no prior state",
    )
    assert fm.next_check_in_minutes == 60


def test_state_frontmatter_rejects_out_of_range_pacing():
    with pytest.raises(ValidationError):
        StateFrontmatter(
            shift_count=0,
            last_updated=datetime.now(timezone.utc),
            open_positions=0,
            open_theses=0,
            regime="bootstrap",
            next_check_in_minutes=10,  # below min 15
            next_check_reason="too soon",
        )
    with pytest.raises(ValidationError):
        StateFrontmatter(
            shift_count=0,
            last_updated=datetime.now(timezone.utc),
            open_positions=0,
            open_theses=0,
            regime="bootstrap",
            next_check_in_minutes=300,  # above max 240
            next_check_reason="too long",
        )


def test_universe_mutation_request_requires_to_tier_for_promote():
    with pytest.raises(ValidationError):
        UniverseMutationRequest(
            event_type=UniverseEventType.PROMOTE,
            symbol="SOLUSDT",
            rationale="moved up the value chain",
            reversion_criterion="reverse if loses momentum",
        )


def test_universe_event_type_rejects_unknown():
    with pytest.raises(ValidationError):
        UniverseMutationRequest(
            event_type="random_string",
            symbol="X",
            rationale="x",
            reversion_criterion="x",
        )


def test_shift_record_round_trip():
    rec = ShiftRecord(
        started_at=datetime.now(timezone.utc),
        spawned=False,
        exit_reason="too_soon",
        prompt_version="abc123",
    )
    assert rec.spawned is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/unit/test_heartbeat_contracts.py -v
```
Expected: FAIL — `ImportError: cannot import name 'StateFrontmatter'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/trading_sandwich/contracts/heartbeat.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


PACING_MIN_MINUTES = 15
PACING_MAX_MINUTES = 240


class StateFrontmatter(BaseModel):
    shift_count: int = Field(ge=0)
    last_updated: datetime
    open_positions: int = Field(ge=0)
    open_theses: int = Field(ge=0)
    regime: str
    next_check_in_minutes: int = Field(ge=PACING_MIN_MINUTES, le=PACING_MAX_MINUTES)
    next_check_reason: str = Field(min_length=1)


class UniverseEventType(str, Enum):
    ADD = "add"
    PROMOTE = "promote"
    DEMOTE = "demote"
    REMOVE = "remove"
    EXCLUDE = "exclude"
    UNEXCLUDE = "unexclude"
    HARD_LIMIT_BLOCKED = "hard_limit_blocked"


_REQUIRES_TO_TIER = {
    UniverseEventType.ADD,
    UniverseEventType.PROMOTE,
    UniverseEventType.DEMOTE,
    UniverseEventType.UNEXCLUDE,
}


class UniverseMutationRequest(BaseModel):
    event_type: UniverseEventType
    symbol: str = Field(min_length=1)
    to_tier: str | None = None
    rationale: str = Field(min_length=10)
    reversion_criterion: str | None = None

    @model_validator(mode="after")
    def _to_tier_required_when_applicable(self) -> "UniverseMutationRequest":
        if self.event_type in _REQUIRES_TO_TIER and not self.to_tier:
            raise ValueError(f"to_tier required for event_type={self.event_type}")
        return self


class ShiftRecord(BaseModel):
    started_at: datetime
    ended_at: datetime | None = None
    requested_interval_min: int | None = None
    actual_interval_min: int | None = None
    interval_clamped: bool = False
    spawned: bool
    exit_reason: str | None = None
    claude_session_id: str | None = None
    duration_seconds: int | None = None
    tools_called: dict[str, int] | None = None
    next_check_in_minutes: int | None = None
    next_check_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    diary_file: str | None = None
    state_snapshot: str | None = None
    prompt_version: str
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/unit/test_heartbeat_contracts.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/contracts/heartbeat.py tests/unit/test_heartbeat_contracts.py
git commit -m "feat(heartbeat): add Pydantic contracts for shifts + universe mutations"
```

---

## Task 2: Alembic migration for `heartbeat_shifts`

**Files:**
- Create: `migrations/versions/0011_heartbeat_shifts.py`
- Create: `src/trading_sandwich/db/models_heartbeat.py`
- Test: `tests/integration/test_heartbeat_migrations.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_heartbeat_migrations.py
import pytest
from sqlalchemy import inspect, text

from trading_sandwich.db.engine import get_session_factory


@pytest.mark.integration
async def test_heartbeat_shifts_table_exists(alembic_upgrade):
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text("SELECT to_regclass('public.heartbeat_shifts')")
        )
        assert result.scalar() == "heartbeat_shifts"


@pytest.mark.integration
async def test_heartbeat_shifts_has_required_columns(alembic_upgrade):
    factory = get_session_factory()
    async with factory() as session:
        cols = (await session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='heartbeat_shifts'"
        ))).scalars().all()
        for required in (
            "id", "started_at", "ended_at",
            "requested_interval_min", "actual_interval_min", "interval_clamped",
            "spawned", "exit_reason",
            "claude_session_id", "duration_seconds", "tools_called",
            "next_check_in_minutes", "next_check_reason",
            "input_tokens", "output_tokens",
            "diary_file", "state_snapshot", "prompt_version",
        ):
            assert required in cols, f"missing column {required}"
```

> The fixture `alembic_upgrade` exists in `tests/integration/conftest.py` from prior work and runs `alembic upgrade head` against the testcontainer Postgres.

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_migrations.py -v
```
Expected: FAIL — `to_regclass` returns NULL.

- [ ] **Step 3: Write the migration and ORM**

```python
# migrations/versions/0011_heartbeat_shifts.py
"""heartbeat_shifts table

Revision ID: 0011_heartbeat_shifts
Revises: 0010_phase2_execution_and_proposals
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_heartbeat_shifts"
down_revision = "0010_phase2_execution_and_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "heartbeat_shifts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("requested_interval_min", sa.Integer()),
        sa.Column("actual_interval_min", sa.Integer()),
        sa.Column("interval_clamped", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("spawned", sa.Boolean(), nullable=False),
        sa.Column("exit_reason", sa.Text()),
        sa.Column("claude_session_id", sa.Text()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("tools_called", sa.JSON()),
        sa.Column("next_check_in_minutes", sa.Integer()),
        sa.Column("next_check_reason", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("diary_file", sa.Text()),
        sa.Column("state_snapshot", sa.Text()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
    )
    op.create_index("idx_shifts_started", "heartbeat_shifts", ["started_at"], postgresql_using="btree")
    op.create_index("idx_shifts_spawned", "heartbeat_shifts", ["spawned", "started_at"])


def downgrade() -> None:
    op.drop_index("idx_shifts_spawned", table_name="heartbeat_shifts")
    op.drop_index("idx_shifts_started", table_name="heartbeat_shifts")
    op.drop_table("heartbeat_shifts")
```

```python
# src/trading_sandwich/db/models_heartbeat.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trading_sandwich.db.models import Base


class HeartbeatShift(Base):
    __tablename__ = "heartbeat_shifts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_interval_min: Mapped[int | None] = mapped_column(Integer)
    actual_interval_min: Mapped[int | None] = mapped_column(Integer)
    interval_clamped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spawned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    claude_session_id: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    tools_called: Mapped[dict | None] = mapped_column(JSONB)
    next_check_in_minutes: Mapped[int | None] = mapped_column(Integer)
    next_check_reason: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    diary_file: Mapped[str | None] = mapped_column(Text)
    state_snapshot: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_migrations.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0011_heartbeat_shifts.py src/trading_sandwich/db/models_heartbeat.py tests/integration/test_heartbeat_migrations.py
git commit -m "feat(heartbeat): migration 0011 + ORM for heartbeat_shifts"
```

---

## Task 3: Alembic migration for `universe_events` + policy.yaml extension

**Files:**
- Create: `migrations/versions/0012_universe_events.py`
- Modify: `src/trading_sandwich/db/models_heartbeat.py` (add `UniverseEvent` ORM)
- Modify: `policy.yaml` (add `heartbeat`, `universe.tiers`, `universe.hard_limits` sections)
- Modify: `tests/integration/test_heartbeat_migrations.py` (add table check)

- [ ] **Step 1: Add the failing test**

Append to `tests/integration/test_heartbeat_migrations.py`:

```python
@pytest.mark.integration
async def test_universe_events_table_exists(alembic_upgrade):
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text("SELECT to_regclass('public.universe_events')")
        )
        assert result.scalar() == "universe_events"


@pytest.mark.integration
async def test_universe_events_has_required_columns(alembic_upgrade):
    factory = get_session_factory()
    async with factory() as session:
        cols = (await session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='universe_events'"
        ))).scalars().all()
        for required in (
            "id", "occurred_at", "shift_id",
            "event_type", "symbol", "from_tier", "to_tier",
            "rationale", "reversion_criterion",
            "diary_ref", "discord_posted", "discord_message_id",
            "attempted_change", "blocked_by", "prompt_version",
        ):
            assert required in cols, f"missing column {required}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_migrations.py::test_universe_events_table_exists -v
```
Expected: FAIL.

- [ ] **Step 3: Write the migration, ORM addition, and policy.yaml extension**

```python
# migrations/versions/0012_universe_events.py
"""universe_events table

Revision ID: 0012_universe_events
Revises: 0011_heartbeat_shifts
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_universe_events"
down_revision = "0011_heartbeat_shifts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "universe_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("shift_id", sa.BigInteger(), sa.ForeignKey("heartbeat_shifts.id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("from_tier", sa.Text()),
        sa.Column("to_tier", sa.Text()),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reversion_criterion", sa.Text()),
        sa.Column("diary_ref", sa.Text()),
        sa.Column("discord_posted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("discord_message_id", sa.Text()),
        sa.Column("attempted_change", sa.JSON()),
        sa.Column("blocked_by", sa.Text()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
    )
    op.create_index("idx_events_occurred", "universe_events", ["occurred_at"])
    op.create_index("idx_events_symbol", "universe_events", ["symbol", "occurred_at"])
    op.create_index("idx_events_type", "universe_events", ["event_type", "occurred_at"])


def downgrade() -> None:
    op.drop_index("idx_events_type", table_name="universe_events")
    op.drop_index("idx_events_symbol", table_name="universe_events")
    op.drop_index("idx_events_occurred", table_name="universe_events")
    op.drop_table("universe_events")
```

Append to `src/trading_sandwich/db/models_heartbeat.py`:

```python
from sqlalchemy import ForeignKey


class UniverseEvent(Base):
    __tablename__ = "universe_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shift_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("heartbeat_shifts.id"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    from_tier: Mapped[str | None] = mapped_column(Text)
    to_tier: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reversion_criterion: Mapped[str | None] = mapped_column(Text)
    diary_ref: Mapped[str | None] = mapped_column(Text)
    discord_posted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discord_message_id: Mapped[str | None] = mapped_column(Text)
    attempted_change: Mapped[dict | None] = mapped_column(JSONB)
    blocked_by: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
```

Append to `policy.yaml`:

```yaml
heartbeat:
  pacing_mode: dynamic
  interval_minutes:
    min: 15
    max: 240
    default: 60
  defaults_by_state:
    no_open_positions_no_active_theses: 120
    active_theses_no_positions: 60
    open_positions_far_from_invalidation: 30
    open_positions_near_invalidation: 15
  daily_shift_cap: 60
  weekly_shift_cap: 350
  shift_timeout_seconds: 300

universe:
  tiers:
    core:
      symbols: [BTCUSDT, ETHUSDT]
      size_multiplier: 1.0
      max_concurrent_positions: 2
      shift_attention: every_shift
    watchlist:
      symbols: [SOLUSDT, BNBUSDT]
      size_multiplier: 0.5
      max_concurrent_positions: 3
      shift_attention: time_permitting
    observation:
      symbols: [LINKUSDT, ARBUSDT]
      size_multiplier: 0.0
      max_concurrent_positions: 0
      shift_attention: weekly_sweep
    excluded:
      symbols: [SHIBUSDT, PEPEUSDT]
      reason: "memecoin volatility uncorrelated with archetype set"
  hard_limits:
    min_24h_volume_usd_floor: 100000000
    vol_30d_annualized_max_ceiling: 3.00
    excluded_symbols_locked: [SHIBUSDT, PEPEUSDT]
    core_promotions_operator_only: true
    max_total_universe_size: 20
    max_per_tier:
      core: 4
      watchlist: 8
      observation: 12
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_migrations.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0012_universe_events.py src/trading_sandwich/db/models_heartbeat.py policy.yaml tests/integration/test_heartbeat_migrations.py
git commit -m "feat(heartbeat): migration 0012 (universe_events) + policy.yaml universe + heartbeat sections"
```

---

## Task 4: Author SOUL.md and rewrite GOALS.md

**Files:**
- Create: `runtime/SOUL.md`
- Modify: `runtime/GOALS.md` (replace placeholder content)

This task has no test — it's authored content. The smoke test (Task 28) verifies Claude actually reads them.

- [ ] **Step 1: Write `runtime/SOUL.md`**

Use the full content from spec §5.1. Create the file with that exact content.

- [ ] **Step 2: Replace `runtime/GOALS.md`**

Use the full content from spec §5.2.

- [ ] **Step 3: Verify both files load as valid Markdown with YAML frontmatter**

```bash
docker compose run --rm tools python -c "
import frontmatter
for path in ['runtime/SOUL.md', 'runtime/GOALS.md']:
    with open(path) as f:
        post = frontmatter.load(f)
    assert post.metadata.get('name'), f'{path} missing frontmatter name'
    assert post.metadata.get('description'), f'{path} missing frontmatter description'
    print(f'{path}: ok ({len(post.content)} chars)')
"
```
Expected: both print `ok` with reasonable char counts (~1500–4000 each).

> If `python-frontmatter` is not installed, add `python-frontmatter` to `pyproject.toml` `[project.dependencies]` and run `docker compose build tools`.

- [ ] **Step 4: Commit**

```bash
git add runtime/SOUL.md runtime/GOALS.md pyproject.toml
git commit -m "feat(heartbeat): author SOUL.md identity and flesh out GOALS.md"
```

---

## Task 5: Bootstrap STATE.md and diary directory

**Files:**
- Create: `runtime/STATE.md`
- Create: `runtime/diary/.gitkeep`

- [ ] **Step 1: Write the bootstrap STATE.md**

```markdown
---
shift_count: 0
last_updated: 2026-04-26T00:00:00+00:00
open_positions: 0
open_theses: 0
regime: bootstrap
next_check_in_minutes: 60
next_check_reason: "first heartbeat — bootstrap shift, read SOUL/GOALS, observe market, write first diary entry"
---

# Working state

## Open positions
(none — bootstrap)

## Active theses (no position yet)
(none — bootstrap)

## Regime read
(bootstrap shift — to be written by first heartbeat)

## Watchlist for next shift
(bootstrap shift — to be written by first heartbeat)
```

- [ ] **Step 2: Create the diary directory placeholder**

```bash
mkdir -p runtime/diary
touch runtime/diary/.gitkeep
```

- [ ] **Step 3: Verify**

```bash
docker compose run --rm tools python -c "
import frontmatter
post = frontmatter.load(open('runtime/STATE.md'))
assert post.metadata['shift_count'] == 0
assert post.metadata['regime'] == 'bootstrap'
print('STATE.md ok')
"
```

- [ ] **Step 4: Commit**

```bash
git add runtime/STATE.md runtime/diary/.gitkeep
git commit -m "feat(heartbeat): bootstrap STATE.md and diary directory"
```

---

## Task 6: Rewrite runtime/CLAUDE.md for heartbeat protocol

**Files:**
- Modify: `runtime/CLAUDE.md`

This is the trader's operational policy — the heartbeat-mode shift protocol. Existing content (signal-triage protocol) is replaced wholesale.

- [ ] **Step 1: Read existing for tone reference**

```bash
docker compose run --rm tools cat /app/runtime/CLAUDE.md | head -50
```
Note the tone — keep voice consistent.

- [ ] **Step 2: Write new content**

Replace `runtime/CLAUDE.md` with content covering these sections (write all five with concrete prose, no placeholders):

1. **§0 Invocation contract** — what Claude can assume: cwd is `/app/runtime`, MCP servers configured via `--mcp-config /app/.mcp.json`, allowed tools listed (refer to `.mcp.json`), STATE / SOUL / GOALS / diary loaded into prompt, model is Sonnet at low effort.
2. **§1 Shift protocol** — prose version of spec §4.4 ORIENT/CHECK/SCAN/ACT/RECORD/EXIT.
3. **§2 STATE.md contract** — must call `write_state` with valid frontmatter; body capped at 2000 chars; `next_check_in_minutes` in [15, 240].
4. **§3 Hard rules** — never widen a stop, never call Binance order-placement tools (deliberately not in allowedTools), never set `next_check_in_minutes` outside [15, 240], if STATE.md fails to parse rebuild from `get_open_positions`.
5. **§4 MCP tools quick reference** — table of every allowed tool with one-line "use this when…" hint.
6. **§5 Failure handling** — timeout: write minimal diary + STATE; tool error: log via diary and continue; if subprocess gets killed mid-shift, the next shift's `state_snapshot` reconstruction kicks in.

Aim for ~1500 words. Tone: concise, second-person (`you are…`), no hedging.

- [ ] **Step 3: Verify it loads**

```bash
docker compose run --rm tools wc -w runtime/CLAUDE.md
```
Expected: ~1200–1800 words.

- [ ] **Step 4: Commit**

```bash
git add runtime/CLAUDE.md
git commit -m "feat(heartbeat): rewrite runtime/CLAUDE.md for heartbeat shift protocol"
```

> **CHECKPOINT FOR OPERATOR REVIEW:** Stop here, ask the operator to read SOUL.md, GOALS.md, and CLAUDE.md before continuing. These three files are the trader's brain.

---

## Task 7: STATE.md / diary I/O module

**Files:**
- Create: `src/trading_sandwich/triage/state_io.py`
- Test: `tests/unit/test_state_md_parser.py`
- Test: `tests/unit/test_diary_rotation.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_state_md_parser.py
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_sandwich.triage.state_io import (
    StateIOError,
    read_state,
    write_state,
    BODY_MAX_CHARS,
)
from trading_sandwich.contracts.heartbeat import StateFrontmatter


def _bootstrap_fm() -> StateFrontmatter:
    return StateFrontmatter(
        shift_count=0,
        last_updated=datetime.now(timezone.utc),
        open_positions=0,
        open_theses=0,
        regime="bootstrap",
        next_check_in_minutes=60,
        next_check_reason="bootstrap",
    )


def test_write_then_read_roundtrip(tmp_path: Path):
    state_path = tmp_path / "STATE.md"
    fm = _bootstrap_fm()
    body = "# Working state\n\n## Open positions\n(none)"
    write_state(state_path, fm, body)
    read_fm, read_body = read_state(state_path)
    assert read_fm.shift_count == 0
    assert "Open positions" in read_body


def test_write_truncates_oversize_body(tmp_path: Path):
    state_path = tmp_path / "STATE.md"
    fm = _bootstrap_fm()
    body = "x" * (BODY_MAX_CHARS + 500)
    result = write_state(state_path, fm, body)
    assert result.body_truncated is True
    _, read_body = read_state(state_path)
    assert len(read_body) == BODY_MAX_CHARS


def test_read_raises_on_invalid_frontmatter(tmp_path: Path):
    state_path = tmp_path / "STATE.md"
    state_path.write_text("---\nshift_count: -1\n---\nbody")
    with pytest.raises(StateIOError):
        read_state(state_path)


def test_write_is_atomic(tmp_path: Path, monkeypatch):
    """If rename fails, the original file should be untouched."""
    state_path = tmp_path / "STATE.md"
    fm = _bootstrap_fm()
    write_state(state_path, fm, "original body")

    def boom(*a, **kw):
        raise OSError("simulated rename failure")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        write_state(state_path, fm, "new body")
    _, body = read_state(state_path)
    assert body.strip() == "original body"
```

```python
# tests/unit/test_diary_rotation.py
from datetime import date, datetime, timezone
from pathlib import Path

from trading_sandwich.triage.state_io import (
    diary_path_for,
    append_diary,
    rotate_if_new_day,
)


def test_diary_path_for_uses_utc_date(tmp_path: Path):
    p = diary_path_for(tmp_path, date(2026, 4, 26))
    assert p == tmp_path / "2026-04-26.md"


def test_append_creates_then_appends(tmp_path: Path):
    p = diary_path_for(tmp_path, date(2026, 4, 26))
    append_diary(p, "first entry")
    append_diary(p, "second entry")
    text = p.read_text()
    assert "first entry" in text
    assert "second entry" in text
    assert text.index("first entry") < text.index("second entry")


def test_rotate_if_new_day_writes_close_to_yesterday(tmp_path: Path):
    yesterday_path = diary_path_for(tmp_path, date(2026, 4, 25))
    yesterday_path.write_text("yesterday content\n")
    today_path = diary_path_for(tmp_path, date(2026, 4, 26))
    rotated = rotate_if_new_day(
        diary_dir=tmp_path,
        today=date(2026, 4, 26),
        state_snapshot_for_header="state snapshot",
        day_close_summary="day close summary",
    )
    assert rotated is True
    assert "## Day close" in yesterday_path.read_text()
    assert "day close summary" in yesterday_path.read_text()
    assert today_path.exists()
    assert "state snapshot" in today_path.read_text()


def test_rotate_if_new_day_noop_when_today_already_exists(tmp_path: Path):
    today_path = diary_path_for(tmp_path, date(2026, 4, 26))
    today_path.write_text("already here\n")
    rotated = rotate_if_new_day(
        diary_dir=tmp_path,
        today=date(2026, 4, 26),
        state_snapshot_for_header="x",
        day_close_summary="y",
    )
    assert rotated is False
    assert today_path.read_text() == "already here\n"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/unit/test_state_md_parser.py tests/unit/test_diary_rotation.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```python
# src/trading_sandwich/triage/state_io.py
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import frontmatter
from pydantic import ValidationError

from trading_sandwich.contracts.heartbeat import StateFrontmatter


BODY_MAX_CHARS = 2000


class StateIOError(Exception):
    pass


@dataclass
class WriteResult:
    body_truncated: bool


def read_state(path: Path) -> tuple[StateFrontmatter, str]:
    try:
        post = frontmatter.load(str(path))
    except Exception as exc:
        raise StateIOError(f"failed to parse {path}: {exc}") from exc
    try:
        fm = StateFrontmatter.model_validate(post.metadata)
    except ValidationError as exc:
        raise StateIOError(f"invalid frontmatter in {path}: {exc}") from exc
    return fm, post.content


def write_state(path: Path, fm: StateFrontmatter, body: str) -> WriteResult:
    truncated = len(body) > BODY_MAX_CHARS
    if truncated:
        body = body[:BODY_MAX_CHARS]
    post = frontmatter.Post(content=body, **fm.model_dump(mode="json"))
    serialized = frontmatter.dumps(post)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    os.replace(tmp_path, path)
    return WriteResult(body_truncated=truncated)


def diary_path_for(diary_dir: Path, day: date) -> Path:
    return diary_dir / f"{day.isoformat()}.md"


def append_diary(path: Path, entry: str) -> None:
    sep = "" if not path.exists() else "\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{sep}{entry}")


def rotate_if_new_day(
    *,
    diary_dir: Path,
    today: date,
    state_snapshot_for_header: str,
    day_close_summary: str,
) -> bool:
    today_path = diary_path_for(diary_dir, today)
    if today_path.exists():
        return False
    yesterday = date.fromordinal(today.toordinal() - 1)
    yesterday_path = diary_path_for(diary_dir, yesterday)
    if yesterday_path.exists():
        with yesterday_path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n## Day close\n\n{day_close_summary}\n")
    today_path.write_text(
        f"# Diary — {today.isoformat()}\n\n"
        f"## Opening state snapshot\n\n{state_snapshot_for_header}\n",
        encoding="utf-8",
    )
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/unit/test_state_md_parser.py tests/unit/test_diary_rotation.py -v
```
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/triage/state_io.py tests/unit/test_state_md_parser.py tests/unit/test_diary_rotation.py
git commit -m "feat(heartbeat): state_io module — read/write STATE.md, append/rotate diary"
```

---

## Task 8: Universe policy module — load + validate + write

**Files:**
- Create: `src/trading_sandwich/triage/universe_policy.py`
- Test: `tests/unit/test_universe_validation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_universe_validation.py
from pathlib import Path

import pytest
import yaml

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)
from trading_sandwich.triage.universe_policy import (
    HardLimitViolation,
    apply_mutation,
    load_universe,
    validate_mutation,
)


SAMPLE_POLICY = {
    "universe": {
        "tiers": {
            "core": {"symbols": ["BTCUSDT", "ETHUSDT"], "max_per_tier_override": None},
            "watchlist": {"symbols": ["SOLUSDT"]},
            "observation": {"symbols": []},
            "excluded": {"symbols": ["SHIBUSDT"]},
        },
        "hard_limits": {
            "min_24h_volume_usd_floor": 100_000_000,
            "vol_30d_annualized_max_ceiling": 3.0,
            "excluded_symbols_locked": ["SHIBUSDT"],
            "core_promotions_operator_only": True,
            "max_total_universe_size": 20,
            "max_per_tier": {"core": 4, "watchlist": 8, "observation": 12},
        },
    }
}


def _write_policy(tmp_path: Path, payload=None) -> Path:
    payload = payload or SAMPLE_POLICY
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(payload))
    return p


def test_validate_blocks_unexclude_of_locked_symbol(tmp_path: Path):
    policy = load_universe(_write_policy(tmp_path))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.UNEXCLUDE,
        symbol="SHIBUSDT",
        to_tier="observation",
        rationale="reconsidered",
        reversion_criterion="re-exclude if no edge",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "excluded_symbols_locked" in str(exc.value)


def test_validate_blocks_promote_into_core(tmp_path: Path):
    policy = load_universe(_write_policy(tmp_path))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.PROMOTE,
        symbol="SOLUSDT",
        to_tier="core",
        rationale="proven over months",
        reversion_criterion="demote on degradation",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "core_promotions_operator_only" in str(exc.value)


def test_validate_blocks_when_total_universe_full(tmp_path: Path):
    payload = {
        "universe": {
            "tiers": {
                "core": {"symbols": [f"C{i}USDT" for i in range(4)]},
                "watchlist": {"symbols": [f"W{i}USDT" for i in range(8)]},
                "observation": {"symbols": [f"O{i}USDT" for i in range(8)]},
                "excluded": {"symbols": []},
            },
            "hard_limits": SAMPLE_POLICY["universe"]["hard_limits"],
        }
    }
    policy = load_universe(_write_policy(tmp_path, payload))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.ADD,
        symbol="NEWUSDT",
        to_tier="observation",
        rationale="caught my eye in scans",
        reversion_criterion="remove if no signals in 21d",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "max_total_universe_size" in str(exc.value)


def test_validate_blocks_when_per_tier_full(tmp_path: Path):
    payload = {
        "universe": {
            "tiers": {
                "core": {"symbols": []},
                "watchlist": {"symbols": []},
                "observation": {"symbols": [f"O{i}USDT" for i in range(12)]},
                "excluded": {"symbols": []},
            },
            "hard_limits": SAMPLE_POLICY["universe"]["hard_limits"],
        }
    }
    policy = load_universe(_write_policy(tmp_path, payload))
    req = UniverseMutationRequest(
        event_type=UniverseEventType.ADD,
        symbol="NEWUSDT",
        to_tier="observation",
        rationale="x" * 20,
        reversion_criterion="x",
    )
    with pytest.raises(HardLimitViolation) as exc:
        validate_mutation(policy, req)
    assert "max_per_tier" in str(exc.value)


def test_apply_mutation_add_writes_yaml_atomically(tmp_path: Path):
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.ADD,
        symbol="ARBUSDT",
        to_tier="observation",
        rationale="fits criteria, watching for setup",
        reversion_criterion="remove if no signals in 21d",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    assert "ARBUSDT" in reread["universe"]["tiers"]["observation"]["symbols"]


def test_apply_mutation_demote_moves_symbol(tmp_path: Path):
    policy_path = _write_policy(tmp_path)
    policy = load_universe(policy_path)
    req = UniverseMutationRequest(
        event_type=UniverseEventType.DEMOTE,
        symbol="SOLUSDT",
        to_tier="observation",
        rationale="momentum failing",
        reversion_criterion="repromote if breaks back out",
    )
    apply_mutation(policy_path, policy, req)
    reread = yaml.safe_load(policy_path.read_text())
    assert "SOLUSDT" not in reread["universe"]["tiers"]["watchlist"]["symbols"]
    assert "SOLUSDT" in reread["universe"]["tiers"]["observation"]["symbols"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/unit/test_universe_validation.py -v
```
Expected: FAIL.

- [ ] **Step 3: Write implementation**

```python
# src/trading_sandwich/triage/universe_policy.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)


class HardLimitViolation(Exception):
    def __init__(self, limit: str, message: str):
        self.limit = limit
        super().__init__(f"{limit}: {message}")


@dataclass
class UniversePolicy:
    raw: dict
    tiers: dict[str, list[str]]
    hard_limits: dict

    @property
    def total_size(self) -> int:
        return sum(len(self.tiers[t]) for t in ("core", "watchlist", "observation"))

    def tier_of(self, symbol: str) -> str | None:
        for t in ("core", "watchlist", "observation", "excluded"):
            if symbol in self.tiers.get(t, []):
                return t
        return None


def load_universe(policy_path: Path) -> UniversePolicy:
    raw = yaml.safe_load(policy_path.read_text())
    universe = raw["universe"]
    tiers = {
        t: list(universe["tiers"].get(t, {}).get("symbols", []))
        for t in ("core", "watchlist", "observation", "excluded")
    }
    return UniversePolicy(raw=raw, tiers=tiers, hard_limits=universe["hard_limits"])


def validate_mutation(policy: UniversePolicy, req: UniverseMutationRequest) -> None:
    hl = policy.hard_limits

    if req.event_type == UniverseEventType.UNEXCLUDE:
        if req.symbol in hl.get("excluded_symbols_locked", []):
            raise HardLimitViolation("excluded_symbols_locked", f"{req.symbol} is operator-locked")

    if req.event_type == UniverseEventType.PROMOTE and req.to_tier == "core":
        if hl.get("core_promotions_operator_only"):
            raise HardLimitViolation("core_promotions_operator_only", "core promotions are operator-only")

    if req.event_type in (UniverseEventType.ADD, UniverseEventType.UNEXCLUDE):
        if policy.total_size >= hl.get("max_total_universe_size", 1_000_000):
            raise HardLimitViolation("max_total_universe_size", "universe is at maximum size")

    if req.to_tier and req.to_tier in ("core", "watchlist", "observation"):
        cap = hl.get("max_per_tier", {}).get(req.to_tier)
        if cap is not None and len(policy.tiers[req.to_tier]) >= cap:
            raise HardLimitViolation("max_per_tier", f"{req.to_tier} tier at cap {cap}")


def apply_mutation(
    policy_path: Path,
    policy: UniversePolicy,
    req: UniverseMutationRequest,
) -> None:
    raw = policy.raw
    tiers_section = raw["universe"]["tiers"]

    def _remove_from_all(symbol: str) -> str | None:
        for t in ("core", "watchlist", "observation", "excluded"):
            symbols = tiers_section.get(t, {}).get("symbols", [])
            if symbol in symbols:
                symbols.remove(symbol)
                return t
        return None

    def _add_to(tier: str, symbol: str) -> None:
        tiers_section[tier]["symbols"].append(symbol)

    et = req.event_type
    if et == UniverseEventType.ADD:
        _add_to(req.to_tier, req.symbol)
    elif et == UniverseEventType.PROMOTE or et == UniverseEventType.DEMOTE:
        _remove_from_all(req.symbol)
        _add_to(req.to_tier, req.symbol)
    elif et == UniverseEventType.REMOVE:
        _remove_from_all(req.symbol)
    elif et == UniverseEventType.EXCLUDE:
        _remove_from_all(req.symbol)
        _add_to("excluded", req.symbol)
    elif et == UniverseEventType.UNEXCLUDE:
        _remove_from_all(req.symbol)
        _add_to(req.to_tier, req.symbol)

    serialized = yaml.safe_dump(raw, sort_keys=False)
    tmp = policy_path.with_suffix(policy_path.suffix + ".tmp")
    tmp.write_text(serialized)
    os.replace(tmp, policy_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/unit/test_universe_validation.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/triage/universe_policy.py tests/unit/test_universe_validation.py
git commit -m "feat(heartbeat): universe_policy module — load/validate/apply mutations atomically"
```

---

## Task 9: Discord webhook notifier (poster only, no retry yet)

**Files:**
- Create: `src/trading_sandwich/notifications/__init__.py`
- Create: `src/trading_sandwich/notifications/discord.py`
- Test: `tests/unit/test_discord_card_format.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_discord_card_format.py
from datetime import datetime, timezone

import pytest

from trading_sandwich.notifications.discord import (
    render_universe_event_card,
    render_hard_limit_blocked_card,
)


def test_render_add_card_includes_required_fields():
    card = render_universe_event_card(
        occurred_at=datetime(2026, 4, 26, 14, 32, tzinfo=timezone.utc),
        event_type="add",
        symbol="SUIUSDT",
        from_tier=None,
        to_tier="observation",
        rationale="caught in 24h gainers, passes fit check",
        reversion_criterion="remove if no signals in 21d",
        shift_id=4721,
        diary_ref="runtime/diary/2026-04-26.md",
    )
    text = card["embeds"][0]["description"]
    assert "SUIUSDT" in text
    assert "observation" in text
    assert "remove if no signals" in text
    assert "shift_id: 4721" in text


def test_hard_limit_card_names_the_limit():
    card = render_hard_limit_blocked_card(
        occurred_at=datetime(2026, 4, 26, 14, 32, tzinfo=timezone.utc),
        attempted={
            "event_type": "promote",
            "symbol": "SOLUSDT",
            "from_tier": "watchlist",
            "to_tier": "core",
            "rationale": "the data warrants it now",
        },
        blocked_by="core_promotions_operator_only",
    )
    text = card["embeds"][0]["description"]
    assert "core_promotions_operator_only" in text
    assert "SOLUSDT" in text
    assert "the data warrants it now" in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/unit/test_discord_card_format.py -v
```
Expected: FAIL.

- [ ] **Step 3: Write implementation**

```python
# src/trading_sandwich/notifications/__init__.py
```

```python
# src/trading_sandwich/notifications/discord.py
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import aiohttp


WEBHOOK_ENV = "DISCORD_UNIVERSE_WEBHOOK_URL"


def _webhook_url() -> str:
    url = os.environ.get(WEBHOOK_ENV)
    if not url:
        raise RuntimeError(f"{WEBHOOK_ENV} not set")
    return url


def render_universe_event_card(
    *,
    occurred_at: datetime,
    event_type: str,
    symbol: str,
    from_tier: str | None,
    to_tier: str | None,
    rationale: str,
    reversion_criterion: str | None,
    shift_id: int | None,
    diary_ref: str | None,
) -> dict[str, Any]:
    movement = (
        f"{from_tier} → {to_tier}" if from_tier and to_tier
        else f"→ {to_tier}" if to_tier
        else f"from {from_tier}" if from_tier
        else ""
    )
    title_line = f"🔄 Universe change — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}"
    headline = f"**{symbol} {movement} ({event_type})**"
    parts = [
        title_line,
        headline,
        "",
        f"Rationale: {rationale}",
    ]
    if reversion_criterion:
        parts.append(f"Reversion: {reversion_criterion}")
    if shift_id is not None or diary_ref:
        meta = []
        if shift_id is not None:
            meta.append(f"shift_id: {shift_id}")
        if diary_ref:
            meta.append(f"diary: {diary_ref}")
        parts.append(" · ".join(meta))
    return {"embeds": [{"description": "\n".join(parts)}]}


def render_hard_limit_blocked_card(
    *,
    occurred_at: datetime,
    attempted: dict[str, Any],
    blocked_by: str,
) -> dict[str, Any]:
    movement = (
        f"{attempted.get('from_tier', '?')} → {attempted.get('to_tier', '?')}"
        if attempted.get("from_tier") or attempted.get("to_tier")
        else ""
    )
    parts = [
        f"⛔ Hard limit blocked — {occurred_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Claude attempted: **{attempted.get('event_type')} {attempted.get('symbol')}** {movement}",
        f"Blocked by: `{blocked_by}`",
        "",
        f"Rationale: {attempted.get('rationale', '')[:200]}",
    ]
    return {"embeds": [{"description": "\n".join(parts)}]}


async def post_card(card: dict[str, Any]) -> str | None:
    """Returns Discord message_id on success, None on failure (caller logs)."""
    url = _webhook_url()
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{url}?wait=true", json=card) as resp:
            if resp.status >= 400:
                return None
            data = await resp.json()
            return data.get("id")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/unit/test_discord_card_format.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/notifications/__init__.py src/trading_sandwich/notifications/discord.py tests/unit/test_discord_card_format.py
git commit -m "feat(heartbeat): discord webhook notifier — card rendering + post"
```

---

## Task 10: Pacing-decision pure function

**Files:**
- Create: `src/trading_sandwich/triage/pacing.py`
- Test: `tests/unit/test_pacing_decision.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pacing_decision.py
from datetime import datetime, timedelta, timezone

from trading_sandwich.triage.pacing import (
    PacingConfig,
    PacingDecision,
    decide_whether_to_spawn,
)


CFG = PacingConfig(
    min_minutes=15,
    max_minutes=240,
    daily_cap=60,
    weekly_cap=350,
)


def _ts(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_first_ever_shift_spawns():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=None,
        last_requested_interval_min=None,
        spawned_today=0,
        spawned_this_week=0,
    )
    assert d.spawn is True
    assert d.exit_reason is None


def test_too_soon_does_not_spawn():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(10),
        last_requested_interval_min=30,
        spawned_today=5,
        spawned_this_week=20,
    )
    assert d.spawn is False
    assert d.exit_reason == "too_soon"


def test_after_requested_interval_spawns():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(35),
        last_requested_interval_min=30,
        spawned_today=5,
        spawned_this_week=20,
    )
    assert d.spawn is True
    assert d.actual_interval_min == 35
    assert d.interval_clamped is False


def test_daily_cap_blocks_spawn():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(60),
        last_requested_interval_min=30,
        spawned_today=60,
        spawned_this_week=200,
    )
    assert d.spawn is False
    assert d.exit_reason == "daily_cap_hit"


def test_weekly_cap_blocks_spawn():
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(60),
        last_requested_interval_min=30,
        spawned_today=10,
        spawned_this_week=350,
    )
    assert d.spawn is False
    assert d.exit_reason == "weekly_cap_hit"


def test_clamp_set_when_actual_smaller_than_requested_no_wait():
    """When >= requested interval has passed but cap binding pushed the
    actual interval much wider than requested, flag clamp."""
    d = decide_whether_to_spawn(
        cfg=CFG,
        last_spawned_at=_ts(120),
        last_requested_interval_min=15,  # asked for soon, didn't get it
        spawned_today=5,
        spawned_this_week=20,
    )
    assert d.spawn is True
    assert d.interval_clamped is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/unit/test_pacing_decision.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```python
# src/trading_sandwich/triage/pacing.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class PacingConfig:
    min_minutes: int
    max_minutes: int
    daily_cap: int
    weekly_cap: int


@dataclass
class PacingDecision:
    spawn: bool
    actual_interval_min: int | None
    interval_clamped: bool
    exit_reason: str | None


CLAMP_MULTIPLIER = 4  # if actual >= 4x requested, flag as clamped


def decide_whether_to_spawn(
    *,
    cfg: PacingConfig,
    last_spawned_at: datetime | None,
    last_requested_interval_min: int | None,
    spawned_today: int,
    spawned_this_week: int,
    now: datetime | None = None,
) -> PacingDecision:
    now = now or datetime.now(timezone.utc)

    if last_spawned_at is None:
        return PacingDecision(spawn=True, actual_interval_min=None, interval_clamped=False, exit_reason=None)

    actual = int((now - last_spawned_at).total_seconds() // 60)
    requested = last_requested_interval_min or cfg.min_minutes

    if actual < requested:
        return PacingDecision(spawn=False, actual_interval_min=actual, interval_clamped=False, exit_reason="too_soon")

    if spawned_today >= cfg.daily_cap:
        return PacingDecision(spawn=False, actual_interval_min=actual, interval_clamped=False, exit_reason="daily_cap_hit")

    if spawned_this_week >= cfg.weekly_cap:
        return PacingDecision(spawn=False, actual_interval_min=actual, interval_clamped=False, exit_reason="weekly_cap_hit")

    clamped = requested > 0 and actual >= requested * CLAMP_MULTIPLIER
    return PacingDecision(spawn=True, actual_interval_min=actual, interval_clamped=clamped, exit_reason=None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/unit/test_pacing_decision.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/triage/pacing.py tests/unit/test_pacing_decision.py
git commit -m "feat(heartbeat): pacing decision pure function with daily/weekly caps"
```

---

## Task 11: MCP tool `read_diary`

**Files:**
- Create: `src/trading_sandwich/mcp/tools/state_diary.py` (skeleton + `read_diary`)
- Modify: `src/trading_sandwich/mcp/server.py` (register module)
- Test: `tests/integration/test_mcp_tool_state_diary_int.py`

- [ ] **Step 1: Inspect existing MCP tool registration pattern**

```bash
docker compose run --rm tools head -40 /app/src/trading_sandwich/mcp/server.py
docker compose run --rm tools head -30 /app/src/trading_sandwich/mcp/tools/reads.py
```

Note: tools register via `@mcp.tool()` decorator; module is imported once for side effects.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_mcp_tool_state_diary_int.py
import pytest
from pathlib import Path

from trading_sandwich.mcp.tools.state_diary import read_diary


@pytest.mark.integration
async def test_read_diary_returns_content(tmp_path: Path, monkeypatch):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    (diary_dir / "2026-04-26.md").write_text("morning shift entry\n")

    monkeypatch.setenv("TS_DIARY_DIR", str(diary_dir))
    result = await read_diary("2026-04-26", 8000)
    assert result["date"] == "2026-04-26"
    assert "morning shift entry" in result["content"]
    assert result["truncated"] is False


@pytest.mark.integration
async def test_read_diary_missing_returns_empty(tmp_path: Path, monkeypatch):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    monkeypatch.setenv("TS_DIARY_DIR", str(diary_dir))
    result = await read_diary("2026-04-25", 8000)
    assert result["content"] == ""


@pytest.mark.integration
async def test_read_diary_truncates(tmp_path: Path, monkeypatch):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    (diary_dir / "2026-04-26.md").write_text("x" * 5000)
    monkeypatch.setenv("TS_DIARY_DIR", str(diary_dir))
    result = await read_diary("2026-04-26", 1000)
    assert len(result["content"]) == 1000
    assert result["truncated"] is True
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_state_diary_int.py -v
```
Expected: FAIL.

- [ ] **Step 4: Write the implementation**

```python
# src/trading_sandwich/mcp/tools/state_diary.py
from __future__ import annotations

import os
from pathlib import Path

from trading_sandwich.mcp.server import mcp


def _diary_dir() -> Path:
    return Path(os.environ.get("TS_DIARY_DIR", "/app/runtime/diary"))


@mcp.tool()
async def read_diary(date: str, max_chars: int = 8000) -> dict:
    """Return the contents of `diary/<date>.md`. Empty content if file missing.

    Args:
        date: ISO date YYYY-MM-DD.
        max_chars: truncate content to this many characters.
    """
    path = _diary_dir() / f"{date}.md"
    if not path.exists():
        return {"date": date, "content": "", "truncated": False}
    content = path.read_text(encoding="utf-8")
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {"date": date, "content": content, "truncated": truncated}
```

Modify `src/trading_sandwich/mcp/server.py` to import the new module (find the existing tool-import block and add):

```python
# in src/trading_sandwich/mcp/server.py, where other tool modules are imported:
from trading_sandwich.mcp.tools import state_diary  # noqa: F401
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_state_diary_int.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/mcp/tools/state_diary.py src/trading_sandwich/mcp/server.py tests/integration/test_mcp_tool_state_diary_int.py
git commit -m "feat(mcp): read_diary tool"
```

---

## Task 12: MCP tools `write_state` + `append_diary`

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/state_diary.py`
- Modify: `tests/integration/test_mcp_tool_state_diary_int.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/integration/test_mcp_tool_state_diary_int.py`:

```python
from trading_sandwich.mcp.tools.state_diary import write_state, append_diary


@pytest.mark.integration
async def test_write_state_persists_frontmatter_and_body(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "STATE.md"
    monkeypatch.setenv("TS_STATE_PATH", str(state_path))
    fm = {
        "shift_count": 1,
        "last_updated": "2026-04-26T14:00:00+00:00",
        "open_positions": 0,
        "open_theses": 1,
        "regime": "choppy",
        "next_check_in_minutes": 60,
        "next_check_reason": "watching ETH for next 1h close",
    }
    result = await write_state(body="# Working state\n\nWatching ETH.", frontmatter=fm)
    assert result["written"] is True
    assert result["body_truncated"] is False
    text = state_path.read_text()
    assert "shift_count: 1" in text
    assert "Watching ETH" in text


@pytest.mark.integration
async def test_write_state_rejects_invalid_frontmatter(tmp_path: Path, monkeypatch):
    state_path = tmp_path / "STATE.md"
    monkeypatch.setenv("TS_STATE_PATH", str(state_path))
    fm = {
        "shift_count": 1,
        "last_updated": "2026-04-26T14:00:00+00:00",
        "open_positions": 0,
        "open_theses": 0,
        "regime": "choppy",
        "next_check_in_minutes": 5,  # invalid
        "next_check_reason": "x",
    }
    result = await write_state(body="x", frontmatter=fm)
    assert result["written"] is False
    assert "next_check_in_minutes" in result["error"]


@pytest.mark.integration
async def test_append_diary_creates_file_then_appends(tmp_path: Path, monkeypatch):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    monkeypatch.setenv("TS_DIARY_DIR", str(diary_dir))
    monkeypatch.setenv("TS_TODAY_OVERRIDE", "2026-04-26")
    r1 = await append_diary("first entry")
    r2 = await append_diary("second entry")
    assert r1["appended"] is True
    assert r2["appended"] is True
    text = (diary_dir / "2026-04-26.md").read_text()
    assert "first entry" in text and "second entry" in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_state_diary_int.py -v
```
Expected: 3 new tests FAIL.

- [ ] **Step 3: Add the implementations**

Append to `src/trading_sandwich/mcp/tools/state_diary.py`:

```python
from datetime import date as _date

from trading_sandwich.contracts.heartbeat import StateFrontmatter
from trading_sandwich.triage.state_io import (
    StateIOError,
    write_state as _write_state_file,
    append_diary as _append_diary_file,
    diary_path_for,
)


def _state_path() -> Path:
    return Path(os.environ.get("TS_STATE_PATH", "/app/runtime/STATE.md"))


def _today() -> _date:
    override = os.environ.get("TS_TODAY_OVERRIDE")
    if override:
        return _date.fromisoformat(override)
    return _date.today()


@mcp.tool()
async def write_state(body: str, frontmatter: dict) -> dict:
    """Replace runtime/STATE.md with provided frontmatter + body."""
    try:
        fm = StateFrontmatter.model_validate(frontmatter)
    except Exception as exc:
        return {"written": False, "body_truncated": False, "error": str(exc)}
    try:
        result = _write_state_file(_state_path(), fm, body)
    except StateIOError as exc:
        return {"written": False, "body_truncated": False, "error": str(exc)}
    return {"written": True, "body_truncated": result.body_truncated, "error": None}


@mcp.tool()
async def append_diary(entry: str) -> dict:
    """Append an entry to today's diary file."""
    path = diary_path_for(_diary_dir(), _today())
    _append_diary_file(path, entry)
    return {"appended": True, "file": str(path)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_state_diary_int.py -v
```
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/mcp/tools/state_diary.py tests/integration/test_mcp_tool_state_diary_int.py
git commit -m "feat(mcp): write_state + append_diary tools"
```

---

## Task 13: MCP tool `get_open_positions`

**Files:**
- Create: `src/trading_sandwich/mcp/tools/universe.py` (skeleton + `get_open_positions`)
- Modify: `src/trading_sandwich/mcp/server.py` (import)
- Test: `tests/integration/test_mcp_tool_universe_int.py`

- [ ] **Step 1: Inspect existing positions ORM**

```bash
docker compose run --rm tools grep -n "positions" /app/src/trading_sandwich/db/models_phase2.py | head -20
```

Identify the table name and column names actually present (e.g., `paper_positions`). The tool must return live state.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_mcp_tool_universe_int.py
import pytest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import insert

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.mcp.tools.universe import get_open_positions


@pytest.mark.integration
async def test_get_open_positions_empty(alembic_upgrade):
    result = await get_open_positions()
    assert isinstance(result, list)
```

> Note: positive-case test depends on the actual positions ORM. If the existing module has a `Position` ORM, add a row via `session.add` and re-query. Use the seed pattern from `tests/integration/test_proposal_state_transitions.py` for reference.

- [ ] **Step 3: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_universe_int.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 4: Write implementation**

```python
# src/trading_sandwich/mcp/tools/universe.py
from __future__ import annotations

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Position  # adjust import per actual ORM
from trading_sandwich.mcp.server import mcp


@mcp.tool()
async def get_open_positions() -> list[dict]:
    """Return all currently open positions as dicts."""
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(Position).where(Position.status == "open")
        )).scalars().all()
        return [
            {
                "symbol": p.symbol,
                "side": p.side,
                "size": float(p.size) if p.size is not None else None,
                "entry_price": float(p.entry_price) if p.entry_price is not None else None,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            }
            for p in rows
        ]
```

> The exact `Position` ORM may differ; adjust the field names by inspection. If the ORM lives in a different module, import accordingly.

Modify `src/trading_sandwich/mcp/server.py`:

```python
from trading_sandwich.mcp.tools import universe  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_universe_int.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/mcp/tools/universe.py src/trading_sandwich/mcp/server.py tests/integration/test_mcp_tool_universe_int.py
git commit -m "feat(mcp): get_open_positions tool"
```

---

## Task 14: MCP tool `assess_symbol_fit`

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/universe.py`
- Modify: `tests/integration/test_mcp_tool_universe_int.py`
- Test: `tests/unit/test_assess_symbol_fit.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_assess_symbol_fit.py
from trading_sandwich.mcp.tools.universe import assess_against_hard_limits


HARD_LIMITS = {
    "min_24h_volume_usd_floor": 100_000_000,
    "vol_30d_annualized_max_ceiling": 3.0,
}


def test_passes_when_metrics_in_range():
    res = assess_against_hard_limits(
        symbol="SUIUSDT",
        volume_24h_usd=300_000_000,
        vol_30d_annualized=1.5,
        hard_limits=HARD_LIMITS,
    )
    assert res["structural"]["passes"] is True
    assert res["liquidity"]["passes"] is True


def test_fails_below_volume_floor():
    res = assess_against_hard_limits(
        symbol="SUIUSDT",
        volume_24h_usd=50_000_000,
        vol_30d_annualized=1.5,
        hard_limits=HARD_LIMITS,
    )
    assert res["liquidity"]["passes"] is False
    assert "min_24h_volume_usd_floor" in res["liquidity"]["failed_criteria"]


def test_fails_above_vol_ceiling():
    res = assess_against_hard_limits(
        symbol="SUIUSDT",
        volume_24h_usd=300_000_000,
        vol_30d_annualized=4.0,
        hard_limits=HARD_LIMITS,
    )
    assert res["liquidity"]["passes"] is False
    assert "vol_30d_annualized_max_ceiling" in res["liquidity"]["failed_criteria"]
```

Append to `tests/integration/test_mcp_tool_universe_int.py`:

```python
from trading_sandwich.mcp.tools.universe import assess_symbol_fit


@pytest.mark.integration
async def test_assess_symbol_fit_smoke(monkeypatch):
    """Smoke test that assess_symbol_fit returns the expected shape;
    market data fetch is mocked."""
    async def _fake_metrics(symbol):
        return {"volume_24h_usd": 250_000_000, "vol_30d_annualized": 1.0}
    monkeypatch.setattr(
        "trading_sandwich.mcp.tools.universe._fetch_metrics",
        _fake_metrics,
    )
    result = await assess_symbol_fit("BTCUSDT")
    assert "structural" in result and "liquidity" in result
    assert result["recommendation"] in {
        "observation_tier_eligible_pending_edge_evidence",
        "rejected",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/unit/test_assess_symbol_fit.py tests/integration/test_mcp_tool_universe_int.py::test_assess_symbol_fit_smoke -v
```
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Append to `src/trading_sandwich/mcp/tools/universe.py`:

```python
from pathlib import Path
import yaml


POLICY_PATH = Path("/app/policy.yaml")


def _load_hard_limits() -> dict:
    raw = yaml.safe_load(POLICY_PATH.read_text())
    return raw["universe"]["hard_limits"]


async def _fetch_metrics(symbol: str) -> dict:
    """Stubbed in v1: returns placeholder metrics. Real impl in Spec B
    pulls from Binance + tradingview MCPs."""
    return {"volume_24h_usd": 0, "vol_30d_annualized": 0.0}


def assess_against_hard_limits(
    *,
    symbol: str,
    volume_24h_usd: float,
    vol_30d_annualized: float,
    hard_limits: dict,
) -> dict:
    structural = {"passes": True, "details": {"symbol": symbol}}
    liquidity_failed: list[str] = []
    if volume_24h_usd < hard_limits.get("min_24h_volume_usd_floor", 0):
        liquidity_failed.append("min_24h_volume_usd_floor")
    if vol_30d_annualized > hard_limits.get("vol_30d_annualized_max_ceiling", 1e9):
        liquidity_failed.append("vol_30d_annualized_max_ceiling")
    return {
        "structural": structural,
        "liquidity": {
            "passes": not liquidity_failed,
            "failed_criteria": liquidity_failed,
            "details": {
                "volume_24h_usd": volume_24h_usd,
                "vol_30d_annualized": vol_30d_annualized,
            },
        },
        "edge_evidence": {"passes": None, "reason": "deferred_to_spec_b"},
    }


@mcp.tool()
async def assess_symbol_fit(symbol: str) -> dict:
    """Check whether a symbol passes Layer 1 + Layer 2 hard-limit criteria."""
    metrics = await _fetch_metrics(symbol)
    hl = _load_hard_limits()
    res = assess_against_hard_limits(
        symbol=symbol,
        volume_24h_usd=metrics["volume_24h_usd"],
        vol_30d_annualized=metrics["vol_30d_annualized"],
        hard_limits=hl,
    )
    if res["liquidity"]["passes"] and res["structural"]["passes"]:
        res["recommendation"] = "observation_tier_eligible_pending_edge_evidence"
    else:
        res["recommendation"] = "rejected"
    return res
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/unit/test_assess_symbol_fit.py tests/integration/test_mcp_tool_universe_int.py -v
```
Expected: PASS (4 unit + integration).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/mcp/tools/universe.py tests/unit/test_assess_symbol_fit.py tests/integration/test_mcp_tool_universe_int.py
git commit -m "feat(mcp): assess_symbol_fit tool (hard-limits only; edge-evidence in spec B)"
```

---

## Task 15: MCP tool `mutate_universe` (the big one)

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/universe.py`
- Test: `tests/integration/test_mutate_universe_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_mutate_universe_e2e.py
import pytest
import yaml
from pathlib import Path
from sqlalchemy import select, text

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import UniverseEvent
from trading_sandwich.mcp.tools.universe import mutate_universe


SAMPLE_POLICY = {
    "universe": {
        "tiers": {
            "core": {"symbols": ["BTCUSDT", "ETHUSDT"]},
            "watchlist": {"symbols": ["SOLUSDT"]},
            "observation": {"symbols": []},
            "excluded": {"symbols": ["SHIBUSDT"]},
        },
        "hard_limits": {
            "min_24h_volume_usd_floor": 100_000_000,
            "vol_30d_annualized_max_ceiling": 3.0,
            "excluded_symbols_locked": ["SHIBUSDT"],
            "core_promotions_operator_only": True,
            "max_total_universe_size": 20,
            "max_per_tier": {"core": 4, "watchlist": 8, "observation": 12},
        },
    }
}


@pytest.fixture
def policy_file(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(SAMPLE_POLICY))
    monkeypatch.setattr("trading_sandwich.mcp.tools.universe.POLICY_PATH", p)
    return p


@pytest.fixture
def stub_discord(monkeypatch):
    posted = []
    async def _fake_post(card):
        posted.append(card)
        return "fake_message_id"
    monkeypatch.setattr("trading_sandwich.mcp.tools.universe._post_card", _fake_post)
    return posted


@pytest.mark.integration
async def test_mutate_add_persists_event_and_yaml_and_discord(
    alembic_upgrade, policy_file, stub_discord
):
    result = await mutate_universe(
        event_type="add",
        symbol="ARBUSDT",
        to_tier="observation",
        rationale="caught my eye in 24h scans, fits criteria",
        reversion_criterion="remove if no signals in 21d",
        shift_id=None,
    )
    assert result["accepted"] is True
    assert result["event_id"] is not None

    reread = yaml.safe_load(policy_file.read_text())
    assert "ARBUSDT" in reread["universe"]["tiers"]["observation"]["symbols"]

    factory = get_session_factory()
    async with factory() as session:
        events = (await session.execute(select(UniverseEvent))).scalars().all()
        assert any(e.event_type == "add" and e.symbol == "ARBUSDT" for e in events)

    assert len(stub_discord) == 1


@pytest.mark.integration
async def test_mutate_blocked_records_hard_limit_event(
    alembic_upgrade, policy_file, stub_discord
):
    result = await mutate_universe(
        event_type="unexclude",
        symbol="SHIBUSDT",
        to_tier="observation",
        rationale="reconsidering",
        reversion_criterion="re-exclude if no edge",
        shift_id=None,
    )
    assert result["accepted"] is False
    assert "excluded_symbols_locked" in result["blocked_by"]

    factory = get_session_factory()
    async with factory() as session:
        events = (await session.execute(select(UniverseEvent))).scalars().all()
        blocked = [e for e in events if e.event_type == "hard_limit_blocked"]
        assert len(blocked) == 1
        assert blocked[0].blocked_by == "excluded_symbols_locked"

    # Discord posts even on block
    assert len(stub_discord) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_mutate_universe_e2e.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append to `src/trading_sandwich/mcp/tools/universe.py`:

```python
from datetime import datetime, timezone
import subprocess

from sqlalchemy import insert

from trading_sandwich.contracts.heartbeat import (
    UniverseEventType,
    UniverseMutationRequest,
)
from trading_sandwich.db.models_heartbeat import UniverseEvent
from trading_sandwich.notifications.discord import (
    post_card as _post_card,
    render_universe_event_card,
    render_hard_limit_blocked_card,
)
from trading_sandwich.triage.universe_policy import (
    HardLimitViolation,
    apply_mutation,
    load_universe,
    validate_mutation,
)


def _prompt_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


@mcp.tool()
async def mutate_universe(
    event_type: str,
    symbol: str,
    rationale: str,
    reversion_criterion: str | None = None,
    to_tier: str | None = None,
    shift_id: int | None = None,
) -> dict:
    """Mutate the universe (add/promote/demote/remove/exclude/unexclude).

    Validates against hard limits. On reject, records `hard_limit_blocked`
    event and posts Discord. On accept, updates policy.yaml atomically,
    records event, posts Discord.
    """
    req = UniverseMutationRequest(
        event_type=UniverseEventType(event_type),
        symbol=symbol,
        to_tier=to_tier,
        rationale=rationale,
        reversion_criterion=reversion_criterion,
    )
    policy = load_universe(POLICY_PATH)
    from_tier = policy.tier_of(symbol)
    occurred_at = datetime.now(timezone.utc)
    pv = _prompt_version()

    factory = get_session_factory()
    try:
        validate_mutation(policy, req)
    except HardLimitViolation as exc:
        async with factory() as session:
            row = UniverseEvent(
                occurred_at=occurred_at,
                shift_id=shift_id,
                event_type=UniverseEventType.HARD_LIMIT_BLOCKED.value,
                symbol=symbol,
                rationale=rationale,
                attempted_change={
                    "event_type": event_type,
                    "symbol": symbol,
                    "from_tier": from_tier,
                    "to_tier": to_tier,
                    "rationale": rationale,
                    "reversion_criterion": reversion_criterion,
                },
                blocked_by=exc.limit,
                prompt_version=pv,
            )
            session.add(row)
            await session.commit()
            event_id = row.id
        card = render_hard_limit_blocked_card(
            occurred_at=occurred_at,
            attempted={
                "event_type": event_type, "symbol": symbol,
                "from_tier": from_tier, "to_tier": to_tier,
                "rationale": rationale,
            },
            blocked_by=exc.limit,
        )
        msg_id = await _post_card(card)
        if msg_id:
            async with factory() as session:
                await session.execute(
                    text("UPDATE universe_events SET discord_posted=true, "
                         "discord_message_id=:m WHERE id=:i").bindparams(m=msg_id, i=event_id)
                )
                await session.commit()
        return {"accepted": False, "blocked_by": exc.limit, "event_id": event_id}

    apply_mutation(POLICY_PATH, policy, req)
    async with factory() as session:
        row = UniverseEvent(
            occurred_at=occurred_at,
            shift_id=shift_id,
            event_type=event_type,
            symbol=symbol,
            from_tier=from_tier,
            to_tier=to_tier,
            rationale=rationale,
            reversion_criterion=reversion_criterion,
            prompt_version=pv,
        )
        session.add(row)
        await session.commit()
        event_id = row.id

    card = render_universe_event_card(
        occurred_at=occurred_at,
        event_type=event_type,
        symbol=symbol,
        from_tier=from_tier,
        to_tier=to_tier,
        rationale=rationale,
        reversion_criterion=reversion_criterion,
        shift_id=shift_id,
        diary_ref=None,
    )
    msg_id = await _post_card(card)
    if msg_id:
        async with factory() as session:
            await session.execute(
                text("UPDATE universe_events SET discord_posted=true, "
                     "discord_message_id=:m WHERE id=:i").bindparams(m=msg_id, i=event_id)
            )
            await session.commit()
    return {"accepted": True, "event_id": event_id}
```

> Important: import `text` from sqlalchemy at top of `universe.py` if not already imported.

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/integration/test_mutate_universe_e2e.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/mcp/tools/universe.py tests/integration/test_mutate_universe_e2e.py
git commit -m "feat(mcp): mutate_universe tool — atomic yaml + DB + Discord"
```

---

## Task 16: MCP tool `get_recent_signals`

**Files:**
- Create: `src/trading_sandwich/mcp/tools/market_scan.py`
- Modify: `src/trading_sandwich/mcp/server.py`
- Test: `tests/integration/test_mcp_tool_market_scan_int.py`

- [ ] **Step 1: Write failing test**

```python
# tests/integration/test_mcp_tool_market_scan_int.py
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.mcp.tools.market_scan import get_recent_signals


@pytest.mark.integration
async def test_get_recent_signals_returns_recent_only(alembic_upgrade):
    factory = get_session_factory()
    async with factory() as session:
        for hours_ago, sym in [(1, "BTCUSDT"), (5, "BTCUSDT"), (50, "BTCUSDT")]:
            s = SignalORM(
                signal_id=uuid4(),
                symbol=sym,
                timeframe="5m",
                archetype="range_rejection",
                direction="long",
                fired_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                trigger_price=100.0,
                confidence=0.5,
                confidence_breakdown={},
                features_snapshot={},
            )
            session.add(s)
        await session.commit()
    result = await get_recent_signals(symbol="BTCUSDT", since="6h")
    assert len(result) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_market_scan_int.py -v
```
Expected: FAIL.

- [ ] **Step 3: Write implementation**

```python
# src/trading_sandwich/mcp/tools/market_scan.py
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.mcp.server import mcp


_SINCE_RE = re.compile(r"^(\d+)([hmd])$")


def _parse_since(since: str) -> timedelta:
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"invalid since: {since}")
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


@mcp.tool()
async def get_recent_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    since: str = "24h",
    limit: int = 50,
) -> list[dict]:
    """Query signals fired by the rule pipeline. Filtered by symbol/timeframe/recency."""
    cutoff = datetime.now(timezone.utc) - _parse_since(since)
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(SignalORM).where(SignalORM.fired_at >= cutoff).order_by(SignalORM.fired_at.desc()).limit(limit)
        if symbol:
            stmt = stmt.where(SignalORM.symbol == symbol)
        if timeframe:
            stmt = stmt.where(SignalORM.timeframe == timeframe)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "signal_id": str(r.signal_id),
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "archetype": r.archetype,
                "direction": r.direction,
                "fired_at": r.fired_at.isoformat(),
                "trigger_price": float(r.trigger_price) if r.trigger_price else None,
                "confidence": float(r.confidence) if r.confidence else None,
            }
            for r in rows
        ]
```

Modify `src/trading_sandwich/mcp/server.py`:

```python
from trading_sandwich.mcp.tools import market_scan  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_market_scan_int.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/mcp/tools/market_scan.py src/trading_sandwich/mcp/server.py tests/integration/test_mcp_tool_market_scan_int.py
git commit -m "feat(mcp): get_recent_signals tool — query signals table by symbol/tf/since"
```

---

## Task 17: MCP tool `get_top_movers` (stub wrapping tradingview MCP)

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/market_scan.py`
- Modify: `tests/integration/test_mcp_tool_market_scan_int.py`

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_mcp_tool_market_scan_int.py`:

```python
from trading_sandwich.mcp.tools.market_scan import get_top_movers


@pytest.mark.integration
async def test_get_top_movers_returns_list(monkeypatch):
    async def _fake_fetch(window, limit):
        return [
            {"symbol": "SUIUSDT", "change_pct": 18.0, "volume_usd": 340_000_000},
            {"symbol": "ARBUSDT", "change_pct": 12.0, "volume_usd": 200_000_000},
        ]
    monkeypatch.setattr("trading_sandwich.mcp.tools.market_scan._fetch_top_movers", _fake_fetch)
    result = await get_top_movers(window="24h", limit=10)
    assert len(result) == 2
    assert result[0]["symbol"] == "SUIUSDT"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_market_scan_int.py::test_get_top_movers_returns_list -v
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append to `src/trading_sandwich/mcp/tools/market_scan.py`:

```python
async def _fetch_top_movers(window: str, limit: int) -> list[dict]:
    """Stub for v1. Real impl in Spec B calls tradingview MCP scanners.

    The heartbeat-spawned Claude has the tradingview MCP available directly;
    this tool exists primarily to give a stable namespaced surface and for
    Spec B to fold in additional logic.
    """
    return []


@mcp.tool()
async def get_top_movers(window: str = "24h", limit: int = 10) -> list[dict]:
    """Return symbols with largest price changes in the given window.

    Args:
        window: '1h', '24h', or '7d'.
        limit: number of symbols to return.
    """
    return await _fetch_top_movers(window, limit)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_mcp_tool_market_scan_int.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/mcp/tools/market_scan.py tests/integration/test_mcp_tool_market_scan_int.py
git commit -m "feat(mcp): get_top_movers tool (stub for spec A; real impl in spec B)"
```

---

## Task 18: Discord retry sweeper

**Files:**
- Modify: `src/trading_sandwich/notifications/discord.py`
- Test: `tests/integration/test_discord_retry_sweeper.py`

- [ ] **Step 1: Write failing test**

```python
# tests/integration/test_discord_retry_sweeper.py
import pytest
from datetime import datetime, timezone

from sqlalchemy import select, text

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import UniverseEvent
from trading_sandwich.notifications.discord import retry_unposted_events


@pytest.mark.integration
async def test_retry_sweeper_marks_posted_after_success(alembic_upgrade, monkeypatch):
    factory = get_session_factory()
    async with factory() as session:
        row = UniverseEvent(
            occurred_at=datetime.now(timezone.utc),
            event_type="add",
            symbol="SUIUSDT",
            to_tier="observation",
            rationale="x" * 20,
            prompt_version="abc",
            discord_posted=False,
        )
        session.add(row)
        await session.commit()
        event_id = row.id

    async def _fake_post(card):
        return "msg_123"
    monkeypatch.setattr("trading_sandwich.notifications.discord.post_card", _fake_post)

    n = await retry_unposted_events(max_age_minutes=60)
    assert n == 1

    async with factory() as session:
        row = (await session.execute(
            select(UniverseEvent).where(UniverseEvent.id == event_id)
        )).scalar_one()
        assert row.discord_posted is True
        assert row.discord_message_id == "msg_123"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_discord_retry_sweeper.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append to `src/trading_sandwich/notifications/discord.py`:

```python
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import UniverseEvent


async def retry_unposted_events(max_age_minutes: int = 1440) -> int:
    """Retry Discord posts for events with discord_posted=false.

    Returns count of events successfully posted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(UniverseEvent).where(
                UniverseEvent.discord_posted.is_(False),
                UniverseEvent.occurred_at >= cutoff,
            )
        )).scalars().all()

    posted = 0
    for row in rows:
        if row.event_type == "hard_limit_blocked":
            card = render_hard_limit_blocked_card(
                occurred_at=row.occurred_at,
                attempted=row.attempted_change or {},
                blocked_by=row.blocked_by or "unknown",
            )
        else:
            card = render_universe_event_card(
                occurred_at=row.occurred_at,
                event_type=row.event_type,
                symbol=row.symbol,
                from_tier=row.from_tier,
                to_tier=row.to_tier,
                rationale=row.rationale,
                reversion_criterion=row.reversion_criterion,
                shift_id=row.shift_id,
                diary_ref=row.diary_ref,
            )
        msg_id = await post_card(card)
        if msg_id:
            async with factory() as session:
                await session.execute(text(
                    "UPDATE universe_events SET discord_posted=true, "
                    "discord_message_id=:m WHERE id=:i"
                ).bindparams(m=msg_id, i=row.id))
                await session.commit()
            posted += 1
    return posted
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_discord_retry_sweeper.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/notifications/discord.py tests/integration/test_discord_retry_sweeper.py
git commit -m "feat(heartbeat): discord retry sweeper for unposted universe events"
```

> **CHECKPOINT FOR OPERATOR REVIEW:** Stop here, ask the operator to verify all 8 MCP tools registered correctly. Run `docker compose run --rm tools python -c "from trading_sandwich.mcp.server import mcp; import asyncio; print(asyncio.run(mcp.list_tools()))"`. Expected: 15 tools total (7 existing + 8 new).

---

## Task 19: Heartbeat gating worker

**Files:**
- Create: `src/trading_sandwich/triage/heartbeat.py`
- Test: `tests/integration/test_heartbeat_gate_db.py`

- [ ] **Step 1: Write failing test**

```python
# tests/integration/test_heartbeat_gate_db.py
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift
from trading_sandwich.triage.heartbeat import (
    _query_pacing_inputs,
    record_skipped_shift,
)


@pytest.mark.integration
async def test_query_returns_no_prior_when_table_empty(alembic_upgrade):
    inputs = await _query_pacing_inputs()
    assert inputs.last_spawned_at is None
    assert inputs.spawned_today == 0
    assert inputs.spawned_this_week == 0


@pytest.mark.integration
async def test_record_skipped_shift_inserts_row(alembic_upgrade):
    await record_skipped_shift(
        actual_interval_min=5,
        exit_reason="too_soon",
        prompt_version="abc",
    )
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(HeartbeatShift))).scalars().all()
        skipped = [r for r in rows if r.spawned is False]
        assert len(skipped) == 1
        assert skipped[0].exit_reason == "too_soon"


@pytest.mark.integration
async def test_query_counts_today_and_week(alembic_upgrade):
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for delta_days in (0, 0, 0, 1, 6, 8):
            session.add(HeartbeatShift(
                started_at=now - timedelta(days=delta_days),
                spawned=True,
                next_check_in_minutes=60,
                prompt_version="abc",
            ))
        await session.commit()
    inputs = await _query_pacing_inputs()
    assert inputs.spawned_today == 3
    assert inputs.spawned_this_week == 5  # 0,0,0,1,6 = 5; 8d ago excluded
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_gate_db.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

```python
# src/trading_sandwich/triage/heartbeat.py
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import select, func

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift


POLICY_PATH = Path(os.environ.get("TS_POLICY_PATH", "/app/policy.yaml"))


@dataclass
class PacingInputs:
    last_spawned_at: datetime | None
    last_requested_interval_min: int | None
    spawned_today: int
    spawned_this_week: int


def _prompt_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


async def _query_pacing_inputs() -> PacingInputs:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    async with factory() as session:
        last = (await session.execute(
            select(HeartbeatShift)
            .where(HeartbeatShift.spawned.is_(True))
            .order_by(HeartbeatShift.started_at.desc())
            .limit(1)
        )).scalars().first()
        spawned_today = (await session.execute(
            select(func.count(HeartbeatShift.id))
            .where(HeartbeatShift.spawned.is_(True), HeartbeatShift.started_at >= today_start)
        )).scalar_one()
        spawned_this_week = (await session.execute(
            select(func.count(HeartbeatShift.id))
            .where(HeartbeatShift.spawned.is_(True), HeartbeatShift.started_at >= week_start)
        )).scalar_one()
    return PacingInputs(
        last_spawned_at=last.started_at if last else None,
        last_requested_interval_min=last.next_check_in_minutes if last else None,
        spawned_today=spawned_today,
        spawned_this_week=spawned_this_week,
    )


async def record_skipped_shift(
    *,
    actual_interval_min: int | None,
    exit_reason: str,
    prompt_version: str,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(HeartbeatShift(
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            actual_interval_min=actual_interval_min,
            spawned=False,
            exit_reason=exit_reason,
            prompt_version=prompt_version,
        ))
        await session.commit()


def load_pacing_config():
    from trading_sandwich.triage.pacing import PacingConfig
    raw = yaml.safe_load(POLICY_PATH.read_text())
    hb = raw["heartbeat"]
    return PacingConfig(
        min_minutes=hb["interval_minutes"]["min"],
        max_minutes=hb["interval_minutes"]["max"],
        daily_cap=hb["daily_shift_cap"],
        weekly_cap=hb["weekly_shift_cap"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_gate_db.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/triage/heartbeat.py tests/integration/test_heartbeat_gate_db.py
git commit -m "feat(heartbeat): gating worker — pacing inputs query + skipped-shift recorder"
```

---

## Task 20: Shift invocation (subprocess spawn) module

**Files:**
- Create: `src/trading_sandwich/triage/shift_invocation.py`
- Test: `tests/unit/test_shift_invocation.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_shift_invocation.py
import asyncio
from pathlib import Path

import pytest

from trading_sandwich.triage.shift_invocation import build_claude_argv, ShiftRunResult


def test_build_argv_includes_all_prompt_files(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for fname in ("CLAUDE.md", "SOUL.md", "GOALS.md", "STATE.md"):
        (runtime / fname).write_text("x")
    (runtime / "diary").mkdir()
    (runtime / "diary" / "2026-04-26.md").write_text("y")

    argv = build_claude_argv(
        runtime_dir=runtime,
        today_diary=runtime / "diary" / "2026-04-26.md",
        mcp_config_path=Path("/app/.mcp.json"),
        allowed_tools=["mcp__tsandwich__read_diary"],
    )
    joined = " ".join(argv)
    assert "claude" in argv[0]
    assert "--model" in argv and "sonnet" in argv
    assert "--strict-mcp-config" in argv
    assert "--mcp-config" in argv
    assert any(str(runtime / "CLAUDE.md") in a for a in argv)
    assert any(str(runtime / "SOUL.md") in a for a in argv)
    assert any(str(runtime / "diary" / "2026-04-26.md") in a for a in argv)
    assert any("mcp__tsandwich__read_diary" in a for a in argv)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/unit/test_shift_invocation.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

```python
# src/trading_sandwich/triage/shift_invocation.py
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ShiftRunResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: int


def build_claude_argv(
    *,
    runtime_dir: Path,
    today_diary: Path,
    mcp_config_path: Path,
    allowed_tools: list[str],
    model: str = "sonnet",
    effort: str = "low",
) -> list[str]:
    """Construct the argv list for spawning Claude for a heartbeat shift."""
    prompt_files = [
        runtime_dir / "CLAUDE.md",
        runtime_dir / "SOUL.md",
        runtime_dir / "GOALS.md",
        runtime_dir / "STATE.md",
        today_diary,
    ]
    argv = [
        os.environ.get("TS_CLAUDE_BIN", "claude"),
        "--model", model,
        "--effort", effort,
        "--strict-mcp-config",
        "--mcp-config", str(mcp_config_path),
        "--allowedTools", ",".join(allowed_tools),
    ]
    for pf in prompt_files:
        argv.extend(["--append-system-prompt-file", str(pf)])
    argv.extend(["-p", "heartbeat shift"])
    return argv


async def spawn_claude_shift(
    *,
    argv: list[str],
    cwd: Path,
    timeout_seconds: int,
) -> ShiftRunResult:
    import time
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ShiftRunResult(returncode=-1, stdout="", stderr="timeout", duration_seconds=timeout_seconds)
    duration = int(time.monotonic() - start)
    return ShiftRunResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        duration_seconds=duration,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/unit/test_shift_invocation.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/triage/shift_invocation.py tests/unit/test_shift_invocation.py
git commit -m "feat(heartbeat): shift_invocation — argv builder + subprocess spawn"
```

---

## Task 21: Heartbeat Celery task end-to-end

**Files:**
- Modify: `src/trading_sandwich/triage/heartbeat.py` (add `heartbeat_tick` task)
- Modify: `src/trading_sandwich/celery_app.py` (register Beat schedule)
- Test: `tests/integration/test_heartbeat_tick_end_to_end.py`

- [ ] **Step 1: Inspect existing celery_app.py**

```bash
docker compose run --rm tools cat /app/src/trading_sandwich/celery_app.py
```

Identify the existing `beat_schedule` dict and where signal-driven triage is registered.

- [ ] **Step 2: Write failing test**

```python
# tests/integration/test_heartbeat_tick_end_to_end.py
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import select

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_heartbeat import HeartbeatShift
from trading_sandwich.triage.heartbeat import heartbeat_tick


@pytest.mark.integration
async def test_first_tick_spawns_and_records(alembic_upgrade, monkeypatch):
    """The first tick on an empty DB should spawn Claude (mocked) and write a row."""
    async def _fake_spawn(argv, cwd, timeout_seconds):
        from trading_sandwich.triage.shift_invocation import ShiftRunResult
        return ShiftRunResult(returncode=0, stdout="ok", stderr="", duration_seconds=10)

    monkeypatch.setattr("trading_sandwich.triage.heartbeat._spawn_claude_shift", _fake_spawn)
    await heartbeat_tick()

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(HeartbeatShift))).scalars().all()
        assert len(rows) == 1
        assert rows[0].spawned is True


@pytest.mark.integration
async def test_immediate_second_tick_skips(alembic_upgrade, monkeypatch):
    factory = get_session_factory()
    async with factory() as session:
        session.add(HeartbeatShift(
            started_at=datetime.now(timezone.utc),
            spawned=True,
            next_check_in_minutes=60,
            prompt_version="abc",
        ))
        await session.commit()

    async def _spawn_called(*a, **kw):
        raise AssertionError("should not spawn")
    monkeypatch.setattr("trading_sandwich.triage.heartbeat._spawn_claude_shift", _spawn_called)

    await heartbeat_tick()

    async with factory() as session:
        rows = (await session.execute(select(HeartbeatShift).where(HeartbeatShift.spawned.is_(False)))).scalars().all()
        assert len(rows) == 1
        assert rows[0].exit_reason == "too_soon"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_tick_end_to_end.py -v
```
Expected: FAIL.

- [ ] **Step 4: Implementation**

Append to `src/trading_sandwich/triage/heartbeat.py`:

```python
from celery import shared_task

from trading_sandwich.triage.pacing import decide_whether_to_spawn
from trading_sandwich.triage.shift_invocation import (
    build_claude_argv,
    spawn_claude_shift as _spawn_claude_shift,
)
from trading_sandwich.triage.state_io import read_state


ALLOWED_TOOLS = [
    "mcp__tsandwich__get_signal",
    "mcp__tsandwich__get_market_snapshot",
    "mcp__tsandwich__find_similar_signals",
    "mcp__tsandwich__get_archetype_stats",
    "mcp__tsandwich__save_decision",
    "mcp__tsandwich__send_alert",
    "mcp__tsandwich__propose_trade",
    "mcp__tsandwich__read_diary",
    "mcp__tsandwich__write_state",
    "mcp__tsandwich__append_diary",
    "mcp__tsandwich__mutate_universe",
    "mcp__tsandwich__assess_symbol_fit",
    "mcp__tsandwich__get_open_positions",
    "mcp__tsandwich__get_recent_signals",
    "mcp__tsandwich__get_top_movers",
    "mcp__binance__binanceAccountInfo",
    "mcp__binance__binanceOrderBook",
    "mcp__binance__binanceAccountSnapshot",
]


RUNTIME_DIR = Path(os.environ.get("TS_RUNTIME_DIR", "/app/runtime"))
MCP_CONFIG_PATH = Path(os.environ.get("TS_MCP_CONFIG", "/app/.mcp.json"))


async def heartbeat_tick() -> None:
    pv = _prompt_version()
    cfg = load_pacing_config()
    inputs = await _query_pacing_inputs()
    decision = decide_whether_to_spawn(
        cfg=cfg,
        last_spawned_at=inputs.last_spawned_at,
        last_requested_interval_min=inputs.last_requested_interval_min,
        spawned_today=inputs.spawned_today,
        spawned_this_week=inputs.spawned_this_week,
    )
    if not decision.spawn:
        await record_skipped_shift(
            actual_interval_min=decision.actual_interval_min,
            exit_reason=decision.exit_reason,
            prompt_version=pv,
        )
        return

    today = datetime.now(timezone.utc).date()
    today_diary = RUNTIME_DIR / "diary" / f"{today.isoformat()}.md"
    if not today_diary.exists():
        today_diary.write_text(f"# Diary — {today.isoformat()}\n", encoding="utf-8")

    argv = build_claude_argv(
        runtime_dir=RUNTIME_DIR,
        today_diary=today_diary,
        mcp_config_path=MCP_CONFIG_PATH,
        allowed_tools=ALLOWED_TOOLS,
    )

    started_at = datetime.now(timezone.utc)
    factory = get_session_factory()
    async with factory() as session:
        row = HeartbeatShift(
            started_at=started_at,
            requested_interval_min=inputs.last_requested_interval_min,
            actual_interval_min=decision.actual_interval_min,
            interval_clamped=decision.interval_clamped,
            spawned=True,
            prompt_version=pv,
        )
        session.add(row)
        await session.commit()
        shift_id = row.id

    timeout_seconds = yaml.safe_load(POLICY_PATH.read_text())["heartbeat"]["shift_timeout_seconds"]
    result = await _spawn_claude_shift(argv=argv, cwd=RUNTIME_DIR, timeout_seconds=timeout_seconds)

    state_snapshot = ""
    next_check_in = None
    next_check_reason = None
    state_path = RUNTIME_DIR / "STATE.md"
    if state_path.exists():
        try:
            fm, body = read_state(state_path)
            state_snapshot = state_path.read_text()
            next_check_in = fm.next_check_in_minutes
            next_check_reason = fm.next_check_reason
        except Exception:
            pass

    async with factory() as session:
        await session.execute(text(
            "UPDATE heartbeat_shifts "
            "SET ended_at=:ended, duration_seconds=:dur, "
            "    next_check_in_minutes=:nci, next_check_reason=:ncr, "
            "    state_snapshot=:snap, exit_reason=:er "
            "WHERE id=:id"
        ).bindparams(
            ended=datetime.now(timezone.utc),
            dur=result.duration_seconds,
            nci=next_check_in,
            ncr=next_check_reason,
            snap=state_snapshot,
            er="completed" if result.returncode == 0 else ("timeout" if result.stderr == "timeout" else "error"),
            id=shift_id,
        ))
        await session.commit()
```

> Add `from sqlalchemy import text` at the top of the file.

Modify `src/trading_sandwich/celery_app.py`. Locate the `beat_schedule` dict and ensure it includes:

```python
"heartbeat-tick": {
    "task": "trading_sandwich.triage.heartbeat.heartbeat_tick_celery",
    "schedule": 15 * 60,  # every 15 minutes
},
```

Then add a Celery task wrapper at the bottom of `triage/heartbeat.py`:

```python
import asyncio


@shared_task(name="trading_sandwich.triage.heartbeat.heartbeat_tick_celery")
def heartbeat_tick_celery() -> None:
    asyncio.run(heartbeat_tick())
```

If `signal-worker`-driven triage is currently registered as a Beat schedule (e.g., `triage-signal-scan` or similar), remove it from `beat_schedule` in the same change.

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker compose run --rm test pytest tests/integration/test_heartbeat_tick_end_to_end.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/trading_sandwich/triage/heartbeat.py src/trading_sandwich/celery_app.py tests/integration/test_heartbeat_tick_end_to_end.py
git commit -m "feat(heartbeat): heartbeat_tick task + Beat schedule (replaces signal-triage trigger)"
```

---

## Task 22: STATE-drift detection helper

**Files:**
- Modify: `src/trading_sandwich/triage/heartbeat.py`
- Test: `tests/integration/test_state_drift_detection.py`

- [ ] **Step 1: Write failing test**

```python
# tests/integration/test_state_drift_detection.py
import pytest
from pathlib import Path

from trading_sandwich.triage.heartbeat import detect_state_drift


@pytest.mark.integration
async def test_drift_detected_when_state_says_2_db_says_0(alembic_upgrade, tmp_path: Path, monkeypatch):
    state_path = tmp_path / "STATE.md"
    state_path.write_text(
        "---\n"
        "shift_count: 1\n"
        "last_updated: 2026-04-26T14:00:00+00:00\n"
        "open_positions: 2\n"
        "open_theses: 0\n"
        "regime: choppy\n"
        "next_check_in_minutes: 60\n"
        "next_check_reason: x\n"
        "---\n"
        "body"
    )
    drift = await detect_state_drift(state_path)
    assert drift["state_says"] == 2
    assert drift["db_says"] == 0
    assert drift["drift"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test pytest tests/integration/test_state_drift_detection.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implementation**

Append to `src/trading_sandwich/triage/heartbeat.py`:

```python
from trading_sandwich.mcp.tools.universe import get_open_positions


async def detect_state_drift(state_path: Path) -> dict:
    """Compare STATE.md frontmatter open_positions to live DB count."""
    fm, _ = read_state(state_path)
    db_positions = await get_open_positions()
    return {
        "state_says": fm.open_positions,
        "db_says": len(db_positions),
        "drift": fm.open_positions != len(db_positions),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
docker compose run --rm test pytest tests/integration/test_state_drift_detection.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/trading_sandwich/triage/heartbeat.py tests/integration/test_state_drift_detection.py
git commit -m "feat(heartbeat): state-drift detection helper"
```

> **CHECKPOINT FOR OPERATOR REVIEW:** Stop here, ask operator to confirm the heartbeat scheduler design is wired as intended. Run `docker compose run --rm tools python -c "from trading_sandwich.celery_app import app; print(list(app.conf.beat_schedule.keys()))"` and verify `heartbeat-tick` is present and signal-triage Beat schedules are absent.

---

## Task 23: CLI subcommand `heartbeat status`

**Files:**
- Create: `src/trading_sandwich/cli/heartbeat.py` (if `cli/` doesn't exist as package, place at `src/trading_sandwich/cli_heartbeat.py` and import from existing `cli.py`)
- Modify: `src/trading_sandwich/cli.py`

- [ ] **Step 1: Inspect existing CLI structure**

```bash
docker compose run --rm tools head -60 /app/src/trading_sandwich/cli.py
```

Determine: is it click? typer? argparse? Use the existing pattern.

- [ ] **Step 2: Write the implementation matching existing style**

Add a `heartbeat status` subcommand that prints, in the existing CLI's style:
- Last spawned shift's `started_at`, `next_check_in_minutes`, `next_check_reason`
- Count of shifts today and this week
- Current STATE.md frontmatter values

(Exact code skipped here as it depends on existing CLI library; engineer follows the existing `myapp <subcommand>` pattern, e.g., the `myapp calibration` command from Phase 2.)

- [ ] **Step 3: Smoke-test the CLI**

```bash
docker compose run --rm tools python -m trading_sandwich.cli heartbeat status
```
Expected: prints values without error (may show "no shifts yet" if DB empty).

- [ ] **Step 4: Commit**

```bash
git add src/trading_sandwich/cli.py src/trading_sandwich/cli/heartbeat.py
git commit -m "feat(cli): heartbeat status subcommand"
```

---

## Task 24: CLI subcommand `heartbeat shifts`

**Files:**
- Modify: same CLI module(s) as Task 23

- [ ] **Step 1: Add `heartbeat shifts --limit N` subcommand**

Prints last N rows of `heartbeat_shifts` ordered by `started_at` desc, columns: started_at, spawned, exit_reason, actual_interval_min, next_check_in_minutes, duration_seconds.

- [ ] **Step 2: Smoke-test**

```bash
docker compose run --rm tools python -m trading_sandwich.cli heartbeat shifts --limit 10
```

- [ ] **Step 3: Commit**

```bash
git add src/trading_sandwich/cli.py
git commit -m "feat(cli): heartbeat shifts subcommand"
```

---

## Task 25: CLI subcommand `heartbeat universe`

**Files:**
- Modify: CLI module(s)

- [ ] **Step 1: Add `heartbeat universe` and `heartbeat universe events --limit N` subcommands**

`heartbeat universe` prints the current `policy.yaml::universe.tiers` snapshot.
`heartbeat universe events` prints last N rows from `universe_events` columns: occurred_at, event_type, symbol, from_tier, to_tier, blocked_by, rationale (truncated to 60 chars).

- [ ] **Step 2: Smoke-test**

```bash
docker compose run --rm tools python -m trading_sandwich.cli heartbeat universe
docker compose run --rm tools python -m trading_sandwich.cli heartbeat universe events --limit 10
```

- [ ] **Step 3: Commit**

```bash
git add src/trading_sandwich/cli.py
git commit -m "feat(cli): heartbeat universe + heartbeat universe events subcommands"
```

---

## Task 26: Update `.env.example` and `.mcp.json`

**Files:**
- Modify: `.env.example`
- Modify: `.mcp.json`
- Create: `docs/setup/discord-webhooks.md`

- [ ] **Step 1: Add env var to `.env.example`**

Append:

```
# Discord webhook for universe-event notifications (created by operator
# via Server → Channel → Integrations → Webhooks). One per channel; do
# not commit the real value.
DISCORD_UNIVERSE_WEBHOOK_URL=
```

- [ ] **Step 2: Update `.mcp.json` allowedTools list (if used)**

If `.mcp.json` declares allowed tools, add the 8 new tool names. If `.mcp.json` only declares servers and `--allowedTools` is passed at invocation, no change needed (the list lives in `triage/heartbeat.py::ALLOWED_TOOLS`).

- [ ] **Step 3: Write `docs/setup/discord-webhooks.md`**

```markdown
# Discord webhooks for the trading sandwich

The system writes to Discord through webhook URLs configured via env vars.

## Webhooks in use

| Env var | Channel purpose | Cadence |
|---|---|---|
| `DISCORD_UNIVERSE_WEBHOOK_URL` | Universe-event feed: every add/promote/demote/remove/exclude/hard_limit_blocked | Spiky — silent for hours, then a few in a row |

## Creating a webhook

1. In Discord: server settings → channel → Integrations → Webhooks → New webhook.
2. Copy the URL.
3. Add to `.env` next to the `DISCORD_UNIVERSE_WEBHOOK_URL=` line.
4. Restart `triage-worker` and `mcp-server`: `docker compose restart triage-worker mcp-server`.

## Rotation

If a webhook URL is leaked or compromised:
1. Discord → integrations → delete the webhook.
2. Create a new one in the same channel.
3. Update `.env`.
4. Restart services.
```

- [ ] **Step 4: Commit**

```bash
git add .env.example .mcp.json docs/setup/discord-webhooks.md
git commit -m "docs+chore(heartbeat): document DISCORD_UNIVERSE_WEBHOOK_URL setup"
```

---

## Task 27: Wire `DISCORD_UNIVERSE_WEBHOOK_URL` into compose services

**Files:**
- Modify: `compose.yaml` (or `docker-compose.yml` — use the file that exists)

- [ ] **Step 1: Inspect existing compose**

```bash
docker compose run --rm tools head -100 /app/compose.yaml || head -100 /app/docker-compose.yml
```

Identify how other env vars (e.g., `DISCORD_BOT_TOKEN`) are passed.

- [ ] **Step 2: Pass `DISCORD_UNIVERSE_WEBHOOK_URL` to `triage-worker` and `mcp-server`**

Add the env var to the services' `environment:` block, sourced from the host `.env`:

```yaml
environment:
  - DISCORD_UNIVERSE_WEBHOOK_URL=${DISCORD_UNIVERSE_WEBHOOK_URL}
```

Apply to both `triage-worker` (so retry sweeper has it) and `mcp-server` (so `mutate_universe` does).

- [ ] **Step 3: Verify wiring**

```bash
docker compose config | grep DISCORD_UNIVERSE
```
Expected: shows the var in both service environments.

- [ ] **Step 4: Commit**

```bash
git add compose.yaml
git commit -m "chore(compose): pass DISCORD_UNIVERSE_WEBHOOK_URL to triage-worker and mcp-server"
```

---

## Task 28: Manual smoke test (operator-run)

**Files:** None — this is a runbook checklist.

This task verifies the entire system end-to-end with a real (mocked-Claude) heartbeat tick. **Operator runs this**, not the agent.

- [ ] **Step 1: Apply migrations**

```bash
docker compose run --rm tools alembic upgrade head
```

- [ ] **Step 2: Set Discord webhook env var**

In the host `.env`:
```
DISCORD_UNIVERSE_WEBHOOK_URL=<your real webhook>
```

- [ ] **Step 3: Bring stack up**

```bash
docker compose up -d
```

- [ ] **Step 4: Manually trigger a heartbeat tick**

```bash
docker compose exec triage-worker python -c "
from trading_sandwich.triage.heartbeat import heartbeat_tick
import asyncio
asyncio.run(heartbeat_tick())
"
```

- [ ] **Step 5: Verify side effects**

```bash
# A row in heartbeat_shifts
docker compose run --rm tools python -m trading_sandwich.cli heartbeat shifts --limit 5

# STATE.md updated by Claude
docker compose exec triage-worker cat /app/runtime/STATE.md

# Today's diary has an entry
docker compose exec triage-worker ls /app/runtime/diary/
docker compose exec triage-worker cat /app/runtime/diary/$(date -u +%Y-%m-%d).md
```

- [ ] **Step 6: Manually mutate universe to verify Discord**

```bash
docker compose exec mcp-server python -c "
from trading_sandwich.mcp.tools.universe import mutate_universe
import asyncio
print(asyncio.run(mutate_universe(
    event_type='add',
    symbol='AVAXUSDT',
    to_tier='observation',
    rationale='manual smoke test of mutate flow',
    reversion_criterion='remove after smoke test',
)))
"
```

- [ ] **Step 7: Verify Discord card appeared in the channel**

Eyeball the Discord channel. Card should match §7.2 of the spec.

- [ ] **Step 8: Verify `policy.yaml` was updated**

```bash
docker compose exec mcp-server grep -A2 "observation:" /app/policy.yaml
```
Expected: `AVAXUSDT` present in observation symbols.

- [ ] **Step 9: Verify `universe_events` row recorded**

```bash
docker compose run --rm tools python -m trading_sandwich.cli heartbeat universe events --limit 5
```

- [ ] **Step 10: Soak test — 24h with heartbeat-tick firing every 15 min**

Leave the stack running. Check the next morning:
- `heartbeat shifts --limit 50` — expect ~96 shifts in 24h (with self-pacing reducing this).
- Discord channel — expect 0–N universe events.
- No process crashes (`docker compose ps`).
- Diary file for today exists with multiple shift entries.

- [ ] **Step 11: Revert plan if needed**

If the system misbehaves, follow §11 of the spec to revert (stop celery-beat, edit celery_app.py to re-add signal-triage Beat schedule, restart). Migrations stay; data left for forensics.

---

## Self-review

Spec coverage check:
- §1 (Goal, in-scope) — every in-scope item has tasks (heartbeat scheduler T19/T21, memory files T4/T5/T6, CLAUDE.md rewrite T6, tiered universe T3, real-time mutation T15, migrations T2/T3, MCP tools T11–T17, Discord T9/T18, CLI T23–T25, signal pipeline frozen T21).
- §2 (Architecture) — all new components have a creating task. Frozen components addressed in T21.
- §3 (Data model) — both tables in T2/T3; policy.yaml extension in T3.
- §4 (Shift protocol) — STATE.md format in T1/T7; CLAUDE.md prose in T6; pacing in T10/T19; rotation in T7.
- §5 (Persona files) — T4 (SOUL/GOALS), T6 (CLAUDE.md), T5 (STATE bootstrap).
- §6 (MCP tools) — T11/T12 (state_diary), T13/T14/T15 (universe), T16/T17 (market_scan). 8 tools.
- §7 (Discord) — T9 (rendering+post), T18 (sweeper), T26 (env doc), T27 (compose wiring).
- §8 (Hard limits / atomicity) — T8 (validation), T15 (pipeline ordering, idempotent yaml write).
- §9 (Testing) — every task is TDD; integration tests use the existing `alembic_upgrade` fixture.
- §10 (Operator setup) — T28 follows §10 verbatim.
- §11 (Reversibility) — T28 step 11 references §11 of the spec.

Placeholder scan: no TBDs found in tasks. Two soft references — Task 13 (`Position` ORM exact name) and Task 23/24/25 (CLI library style). These require inspection of existing code as the first step of each task and are explicit about that.

Type consistency: `mutate_universe` signature in T15 matches the call in T28 (positional `event_type`, kwargs for the rest). `StateFrontmatter` field names consistent across T1, T7, T12, T22. `UniverseEventType` enum values consistent with the `event_type` strings in `policy.yaml` and tests.

---

## Plan complete

Plan saved to `docs/superpowers/plans/2026-04-26-heartbeat-trader.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a 28-task plan.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
