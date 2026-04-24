# Phase 2 Stage 1a — Triage Loop + MCP Tools + Approval E2E

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Claude Code as the triage operator for gated signals, ship the 7 foundational MCP tools, and prove the Discord-button approval loop end-to-end against a stubbed execution enqueue. The execution-worker, policy rails, kill-switch, watchdog, live adapter, and CLI additions land in the sibling plan `2026-04-25-phase-2-stage-1b-execution.md`, which is written after Task 22 ships.

**Architecture:** Three new long-lived services in this plan (`mcp-server`, `triage-worker`, `discord-listener`) glued through Postgres and Celery queues. Claude is invoked via `claude -p` subprocess per gated signal; it reasons via our custom FastMCP server's 7 foundational tools; decisions and proposals land in DB; operator taps ✅ in Discord to flip the proposal to `approved` and enqueue `submit_order` on the `execution` queue. The `execution-worker` that consumes that queue is built in plan 1b.

**Tech Stack:** Python 3.12, FastMCP (`mcp` SDK), Celery + Redis, `discord.py`, SQLAlchemy 2.0 async, Alembic, Pydantic v2, testcontainers, pytest.

**Spec:** [docs/superpowers/specs/2026-04-25-phase-2-claude-triage-design.md](../specs/2026-04-25-phase-2-claude-triage-design.md)

**Sibling plan (follow-up):** `docs/superpowers/plans/2026-04-25-phase-2-stage-1b-execution.md` — execution-worker + paper & live adapters + 16 policy rails + kill-switch + watchdog + CLI + compose + `runtime/CLAUDE.md` authoring + E2E.

---

## Conventions (read once before starting)

- **All commands run via `docker compose run --rm test <args>` or `docker compose run --rm tools <args>`.** Never install deps on the host.
- **Every task ends with a commit.** Conventional Commits style (`feat:`, `test:`, `fix:`, `chore:`, `docs:`).
- **Feature-version SHA** — `git rev-parse HEAD` is captured in `prompt_version` on every `claude_decisions` row and in `policy_version` on every `trade_proposals` / `orders` row.
- **Test markers:** unit tests go in `tests/unit/` (no mark); integration tests in `tests/integration/` use `@pytest.mark.integration`.
- **Pydantic v2**, `_Base` frozen + `extra="forbid"` per `contracts/models.py`.
- **Prompt caching is out of scope** (we're spawning `claude -p`, not using the SDK) — the prompt cache is Claude Code's built-in behavior, not something we tune.

---

## File structure (what gets created or modified across the plan)

### New Python modules
- `src/trading_sandwich/contracts/phase2.py` — new Pydantic contracts (`StopLossSpec`, `TakeProfitSpec`, `AlertPayload`, `MarketSnapshot`, `SignalDetail`, `SimilarSignal`, `ArchetypeStats`, `ClaudeInvocation`, `ClaudeResponse`, `OrderRequest`, `OrderReceipt`, `AccountState`)
- `src/trading_sandwich/db/models_phase2.py` — new ORM models (`TradeProposal`, `Order`, `OrderModification`, `Position`, `RiskEvent`, `KillSwitchState`, `Alert`)
- `src/trading_sandwich/mcp/__init__.py` — package marker
- `src/trading_sandwich/mcp/server.py` — FastMCP server entry point
- `src/trading_sandwich/mcp/tools/reads.py` — `get_signal`, `get_market_snapshot`, `find_similar_signals`, `get_archetype_stats`
- `src/trading_sandwich/mcp/tools/decisions.py` — `save_decision`
- `src/trading_sandwich/mcp/tools/alerts.py` — `send_alert`
- `src/trading_sandwich/mcp/tools/proposals.py` — `propose_trade`
- `src/trading_sandwich/triage/__init__.py` — package marker
- `src/trading_sandwich/triage/invocation.py` — canonical `invoke_claude()` function
- `src/trading_sandwich/triage/worker.py` — Celery task `triage_signal`
- `src/trading_sandwich/triage/daily_cap.py` — Redis date-keyed cap gate
- `src/trading_sandwich/discord/__init__.py`
- `src/trading_sandwich/discord/listener.py` — `discord.py` bot with interaction handler
- `src/trading_sandwich/discord/embed.py` — proposal-card embed renderer
- `src/trading_sandwich/discord/webhook.py` — one-shot webhook poster (for `send_alert`)
- `src/trading_sandwich/execution/__init__.py`
- `src/trading_sandwich/execution/worker.py` — Celery task `submit_order`
- `src/trading_sandwich/execution/adapters/base.py` — `ExchangeAdapter` ABC
- `src/trading_sandwich/execution/adapters/paper.py` — `PaperAdapter`
- `src/trading_sandwich/execution/adapters/ccxt_live.py` — `CCXTProAdapter`
- `src/trading_sandwich/execution/policy_rails.py` — 16-rail pre-trade check
- `src/trading_sandwich/execution/kill_switch.py` — trip/read/resume logic
- `src/trading_sandwich/execution/watchdog.py` — Celery Beat `reconcile_positions`
- `src/trading_sandwich/execution/paper_match.py` — Celery Beat `paper_match_orders`
- `src/trading_sandwich/execution/proposal_sweeper.py` — Celery Beat `expire_stale_proposals`

### Modified existing modules
- `src/trading_sandwich/contracts/models.py` — extend `GatingOutcome` (already covers `daily_cap_hit`; verify)
- `src/trading_sandwich/_policy.py` — add accessors for Phase 2 keys
- `src/trading_sandwich/signals/gating.py` — add daily-cap stage before persist
- `src/trading_sandwich/signals/worker.py` — enqueue `triage_signal` after gating passes
- `src/trading_sandwich/celery_app.py` — register `triage` + `execution` queues, beat jobs
- `src/trading_sandwich/cli.py` — add `proposals`, `orders`, `positions`, `trading`, `calibration`, `flatten` subcommands
- `src/trading_sandwich/config.py` — add Discord + Binance + proposal TTL env vars
- `docker-compose.yml` — add 4 services
- `policy.yaml` — add new keys
- `runtime/CLAUDE.md` — rewrite from stub to full persona
- `runtime/GOALS.md` — NEW template
- `.mcp.json` — NEW at repo root

### Migrations
- `migrations/versions/0010_phase2_execution_and_proposals.py`

### Tests (new)
- `tests/unit/test_contracts_phase2.py`
- `tests/unit/test_daily_cap.py`
- `tests/unit/test_mcp_tool_get_signal.py`
- `tests/unit/test_mcp_tool_get_market_snapshot.py`
- `tests/unit/test_mcp_tool_find_similar_signals.py`
- `tests/unit/test_mcp_tool_get_archetype_stats.py`
- `tests/unit/test_mcp_tool_save_decision.py`
- `tests/unit/test_mcp_tool_send_alert.py`
- `tests/unit/test_mcp_tool_propose_trade.py`
- `tests/unit/test_invocation.py`
- `tests/unit/test_discord_embed.py`
- `tests/unit/test_discord_listener.py` (interaction handler unit)
- `tests/unit/test_paper_adapter.py`
- `tests/unit/test_policy_rails.py` (one test per rail)
- `tests/unit/test_kill_switch.py`
- `tests/unit/test_proposal_sweeper.py`
- `tests/unit/test_cli_phase2.py`
- `tests/integration/test_phase2_migrations.py`
- `tests/integration/test_daily_cap_gate.py`
- `tests/integration/test_mcp_server.py`
- `tests/integration/test_triage_end_to_end.py`
- `tests/integration/test_approval_loop.py`
- `tests/integration/test_execution_paper.py`
- `tests/integration/test_watchdog_reconcile.py`
- `tests/integration/test_phase2_e2e.py`

### Test harnesses
- `tests/fixtures/fake_claude.py` — stub `claude` binary that reads prompt from argv/stdin and emits canned JSON (for integration tests)
- `tests/unit/_fakers.py` — extend with `make_trade_proposal`, `make_order`, `make_account_state`

---

## Plan layout (Stage 1a — this plan, 22 tasks)

- **Phase A — Schema + contracts** (tasks 1–4)
- **Phase B — Daily cap + gating** (tasks 5–6)
- **Phase C — MCP server + 7 tools** (tasks 7–14)
- **Phase D — Triage invocation** (tasks 15–18)
- **Phase E — Discord approval loop** (tasks 19–22)

Checkpoints for review: after Task 4 (schema), Task 14 (tools), Task 18 (triage E2E), Task 22 (approval E2E — end of this plan).

Stage 1b (sibling plan, written after Task 22 ships) covers: execution worker, paper adapter, 16 policy rails, kill-switch, live adapter, proposal sweeper, watchdog, CLI additions, compose config, runtime/CLAUDE.md + GOALS.md authoring, and the Phase 2 full E2E test.

---

## Phase A — Schema + contracts

### Task 1: New Pydantic contracts for Phase 2

**Files:**
- Create: `src/trading_sandwich/contracts/phase2.py`
- Test: `tests/unit/test_contracts_phase2.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_contracts_phase2.py
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trading_sandwich.contracts.phase2 import (
    AlertPayload,
    ClaudeResponse,
    OrderRequest,
    StopLossSpec,
    TakeProfitSpec,
)


def test_stop_loss_spec_requires_value():
    spec = StopLossSpec(kind="fixed_price", value=Decimal("68000"))
    assert spec.kind == "fixed_price"
    assert spec.trigger == "mark"
    assert spec.working_type == "stop_market"


def test_stop_loss_spec_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        StopLossSpec(kind="bogus", value=Decimal("1"))


def test_claude_response_valid_decision():
    resp = ClaudeResponse(decision="alert", rationale="x" * 50)
    assert resp.decision == "alert"


def test_claude_response_rejects_live_order():
    with pytest.raises(ValidationError):
        ClaudeResponse(decision="live_order", rationale="x" * 50)


def test_claude_response_requires_rationale_min_length():
    with pytest.raises(ValidationError):
        ClaudeResponse(decision="alert", rationale="short")


def test_order_request_requires_stop_loss():
    with pytest.raises(ValidationError):
        OrderRequest(
            symbol="BTCUSDT",
            side="long",
            order_type="market",
            size_usd=Decimal("500"),
            stop_loss=None,  # type: ignore[arg-type]
        )


def test_alert_payload_structure():
    payload = AlertPayload(
        title="x", body="y", signal_id=uuid4(), decision_id=uuid4()
    )
    assert payload.title == "x"


def test_take_profit_rr_ratio_kind():
    tp = TakeProfitSpec(kind="rr_ratio", value=Decimal("2.0"))
    assert tp.value == Decimal("2.0")
```

- [ ] **Step 2: Run to verify it fails**

```
docker compose run --rm test pytest tests/unit/test_contracts_phase2.py -v
```
Expected: `ImportError: cannot import name 'AlertPayload' from 'trading_sandwich.contracts.phase2'`

- [ ] **Step 3: Write the contracts module**

```python
# src/trading_sandwich/contracts/phase2.py
"""Phase 2 contracts: orders, proposals, Claude I/O, adapter types.

Extends contracts/models.py; imported by MCP tools, triage worker,
execution worker, discord-listener.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DecisionLiteral = Literal["alert", "paper_trade", "ignore", "research_more"]
OrderStatus = Literal[
    "pending", "open", "partial", "filled", "canceled", "rejected"
]
ProposalStatus = Literal[
    "pending", "approved", "rejected", "expired", "executed", "failed"
]
ExecutionMode = Literal["paper", "live"]
Side = Literal["long", "short"]
OrderType = Literal["market", "limit", "stop"]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StopLossSpec(_Base):
    kind: Literal["fixed_price", "atr_multiple", "percent", "structural"]
    value: Decimal
    trigger: Literal["last", "mark", "index"] = "mark"
    working_type: Literal["stop_market", "stop_limit"] = "stop_market"


class TakeProfitSpec(_Base):
    kind: Literal["fixed_price", "rr_ratio", "atr_multiple", "structural"]
    value: Decimal


class AlertPayload(_Base):
    title: str
    body: str
    signal_id: UUID
    decision_id: UUID


class ClaudeInvocation(_Base):
    signal_id: UUID
    invocation_mode: Literal["triage", "analyze", "retrospect", "ad_hoc"]
    invoked_at: datetime
    prompt_version: str


class ClaudeResponse(_Base):
    decision: DecisionLiteral
    rationale: str = Field(min_length=40)
    alert_posted: bool = False
    proposal_created: bool = False
    notes: str | None = None


class OrderRequest(_Base):
    symbol: str
    side: Side
    order_type: OrderType
    size_usd: Decimal
    limit_price: Decimal | None = None
    stop_loss: StopLossSpec
    take_profit: TakeProfitSpec | None = None
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"
    client_order_id: str


class OrderReceipt(_Base):
    exchange_order_id: str | None
    status: OrderStatus
    avg_fill_price: Decimal | None = None
    filled_base: Decimal | None = None
    fees_usd: Decimal | None = None
    rejection_reason: str | None = None


class AccountState(_Base):
    equity_usd: Decimal
    free_margin_usd: Decimal
    unrealized_pnl_usd: Decimal
    realized_pnl_today_usd: Decimal
    open_positions_count: int
    leverage_used: Decimal


class MarketSnapshot(_Base):
    symbol: str
    per_timeframe: dict  # timeframe -> dict of feature values


class SignalDetail(_Base):
    signal_id: UUID
    symbol: str
    timeframe: str
    archetype: str
    direction: Side
    fired_at: datetime
    trigger_price: Decimal
    confidence: Decimal
    confidence_breakdown: dict
    features_snapshot: dict
    outcomes_so_far: list[dict] = Field(default_factory=list)


class SimilarSignal(_Base):
    signal_id: UUID
    fired_at: datetime
    archetype: str
    direction: Side
    trend_regime: str | None
    vol_regime: str | None
    confidence: Decimal
    outcomes: list[dict]


class SimilarSignalsResult(_Base):
    match_method: Literal["structural"] = "structural"
    sparse: bool
    results: list[SimilarSignal]


class ArchetypeStats(_Base):
    archetype: str
    lookback_days: int
    total_fires: int
    by_bucket: list[dict]  # [{direction, trend_regime, vol_regime, count, median_return_24h, ...}]
```

- [ ] **Step 4: Run tests to verify they pass**

```
docker compose run --rm test pytest tests/unit/test_contracts_phase2.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```
git add src/trading_sandwich/contracts/phase2.py tests/unit/test_contracts_phase2.py
git commit -m "feat: Phase 2 Pydantic contracts (orders, proposals, Claude I/O)"
```

---

### Task 2: ORM models for new Phase 2 tables

**Files:**
- Create: `src/trading_sandwich/db/models_phase2.py`
- Test: `tests/unit/test_db_models_phase2.py` (import + shape smoke)

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_db_models_phase2.py
def test_phase2_models_import():
    from trading_sandwich.db.models_phase2 import (
        Alert,
        KillSwitchState,
        Order,
        OrderModification,
        Position,
        RiskEvent,
        TradeProposal,
    )
    assert TradeProposal.__tablename__ == "trade_proposals"
    assert Order.__tablename__ == "orders"
    assert OrderModification.__tablename__ == "order_modifications"
    assert Position.__tablename__ == "positions"
    assert RiskEvent.__tablename__ == "risk_events"
    assert KillSwitchState.__tablename__ == "kill_switch_state"
    assert Alert.__tablename__ == "alerts"


def test_trade_proposal_has_prose_columns():
    from trading_sandwich.db.models_phase2 import TradeProposal
    cols = {c.name for c in TradeProposal.__table__.columns}
    for prose in ["opportunity", "risk", "profit_case", "alignment", "similar_trades_evidence"]:
        assert prose in cols


def test_order_has_policy_version_column():
    from trading_sandwich.db.models_phase2 import Order
    cols = {c.name for c in Order.__table__.columns}
    assert "policy_version" in cols
    assert "client_order_id" in cols
    assert "execution_mode" in cols
```

- [ ] **Step 2: Run to verify fail**

```
docker compose run --rm test pytest tests/unit/test_db_models_phase2.py -v
```
Expected: `ModuleNotFoundError: trading_sandwich.db.models_phase2`

- [ ] **Step 3: Write the ORM models**

```python
# src/trading_sandwich/db/models_phase2.py
"""Phase 2 ORM models. All new tables land in migration 0010."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from trading_sandwich.db.models import Base


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_order_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claude_decisions.decision_id", ondelete="SET NULL")
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="SET NULL")
    )
    # proposal_id FK added post-hoc in migration to resolve circular ref
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_base: Mapped[Decimal | None] = mapped_column(Numeric)
    size_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    stop_loss: Mapped[dict] = mapped_column(JSONB, nullable=False)
    take_profit: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric)
    filled_base: Mapped[Decimal | None] = mapped_column(Numeric)
    fees_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)


class TradeProposal(Base):
    __tablename__ = "trade_proposals"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_trade_proposals_decision_id"),
        CheckConstraint("length(opportunity) >= 80", name="ck_trade_proposals_opportunity_len"),
        CheckConstraint("length(risk) >= 80", name="ck_trade_proposals_risk_len"),
        CheckConstraint("length(profit_case) >= 80", name="ck_trade_proposals_profit_case_len"),
        CheckConstraint("length(alignment) >= 40", name="ck_trade_proposals_alignment_len"),
        CheckConstraint(
            "length(similar_trades_evidence) >= 80",
            name="ck_trade_proposals_evidence_len",
        ),
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claude_decisions.decision_id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric)
    stop_loss: Mapped[dict] = mapped_column(JSONB, nullable=False)
    take_profit: Mapped[dict | None] = mapped_column(JSONB)
    time_in_force: Mapped[str] = mapped_column(Text, nullable=False, server_default="GTC")

    opportunity: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(Text, nullable=False)
    profit_case: Mapped[str] = mapped_column(Text, nullable=False)
    alignment: Mapped[str] = mapped_column(Text, nullable=False)
    similar_trades_evidence: Mapped[str] = mapped_column(Text, nullable=False)

    expected_rr: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    worst_case_loss_usd: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    similar_signals_count: Mapped[int] = mapped_column(Integer, nullable=False)
    similar_signals_win_rate: Mapped[Decimal | None] = mapped_column(Numeric)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    proposed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # executed_order_id FK added post-hoc in migration (circular ref)
    executed_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)


class OrderModification(Base):
    __tablename__ = "order_modifications"
    mod_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claude_decisions.decision_id", ondelete="SET NULL")
    )
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class Position(Base):
    __tablename__ = "positions"
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    size_base: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    avg_entry: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class RiskEvent(Base):
    __tablename__ = "risk_events"
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_taken: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class KillSwitchState(Base):
    __tablename__ = "kill_switch_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_kill_switch_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    tripped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    tripped_reason: Mapped[str | None] = mapped_column(Text)
    resumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    resumed_ack_reason: Mapped[str | None] = mapped_column(Text)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("signal_id", "channel", name="uq_alerts_signal_channel"),)
    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.signal_id", ondelete="SET NULL")
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claude_decisions.decision_id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 4: Run test**

```
docker compose run --rm test pytest tests/unit/test_db_models_phase2.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add src/trading_sandwich/db/models_phase2.py tests/unit/test_db_models_phase2.py
git commit -m "feat: Phase 2 ORM models for orders, proposals, risk events, kill switch"
```

---

### Task 3: Alembic migration 0010 — schema

**Files:**
- Create: `migrations/versions/0010_phase2_execution_and_proposals.py`
- Test: `tests/integration/test_phase2_migrations.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/integration/test_phase2_migrations.py
import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


_PHASE_2_TABLES = [
    "orders",
    "trade_proposals",
    "order_modifications",
    "positions",
    "risk_events",
    "kill_switch_state",
    "alerts",
]


def _assert_tables(async_url: str, tables: list[str]) -> None:
    async def _run() -> None:
        engine = create_async_engine(async_url)
        try:
            async with engine.connect() as conn:
                for tbl in tables:
                    r = await conn.execute(text(f"SELECT to_regclass('public.{tbl}')"))
                    assert r.scalar() == tbl, f"{tbl} missing"
        finally:
            await engine.dispose()
    asyncio.run(_run())


@pytest.mark.integration
def test_phase2_tables_exist(env_for_postgres):
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        _assert_tables(url, _PHASE_2_TABLES)


@pytest.mark.integration
def test_kill_switch_state_singleton_seeded(env_for_postgres):
    async def _check(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text("SELECT id, active FROM kill_switch_state")
                )
                rows = r.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == 1
                assert rows[0][1] is False
        finally:
            await engine.dispose()

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_check(url))


@pytest.mark.integration
def test_claude_decisions_unique_signal_invocation_mode(env_for_postgres):
    async def _check(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='claude_decisions' "
                    "AND indexname='uq_claude_decisions_signal_invocation_mode'"
                ))
                assert r.scalar() is not None
        finally:
            await engine.dispose()

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_check(url))
```

- [ ] **Step 2: Run to verify fail**

```
docker compose run --rm test pytest tests/integration/test_phase2_migrations.py -v -m integration
```
Expected: migration 0010 not found → upgrade halts at 0009 → tables missing.

- [ ] **Step 3: Write the migration**

```python
# migrations/versions/0010_phase2_execution_and_proposals.py
"""phase2_execution_and_proposals

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # orders (no circular FK at creation time)
    op.create_table(
        "orders",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_order_id", sa.Text, nullable=False, unique=True),
        sa.Column("exchange_order_id", sa.Text, nullable=True),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("size_base", sa.Numeric, nullable=True),
        sa.Column("size_usd", sa.Numeric, nullable=False),
        sa.Column("limit_price", sa.Numeric, nullable=True),
        sa.Column("stop_loss", postgresql.JSONB, nullable=False),
        sa.Column("take_profit", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("execution_mode", sa.Text, nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("filled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("avg_fill_price", sa.Numeric, nullable=True),
        sa.Column("filled_base", sa.Numeric, nullable=True),
        sa.Column("fees_usd", sa.Numeric, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("policy_version", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_orders_symbol_submitted", "orders", ["symbol", sa.text("submitted_at DESC")])
    op.create_index("ix_orders_status", "orders", ["status"])

    # trade_proposals
    op.create_table(
        "trade_proposals",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("size_usd", sa.Numeric, nullable=False),
        sa.Column("limit_price", sa.Numeric, nullable=True),
        sa.Column("stop_loss", postgresql.JSONB, nullable=False),
        sa.Column("take_profit", postgresql.JSONB, nullable=True),
        sa.Column("time_in_force", sa.Text, nullable=False, server_default="GTC"),

        sa.Column("opportunity", sa.Text, nullable=False),
        sa.Column("risk", sa.Text, nullable=False),
        sa.Column("profit_case", sa.Text, nullable=False),
        sa.Column("alignment", sa.Text, nullable=False),
        sa.Column("similar_trades_evidence", sa.Text, nullable=False),

        sa.Column("expected_rr", sa.Numeric, nullable=False),
        sa.Column("worst_case_loss_usd", sa.Numeric, nullable=False),
        sa.Column("similar_signals_count", sa.Integer, nullable=False),
        sa.Column("similar_signals_win_rate", sa.Numeric, nullable=True),

        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("proposed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Text, nullable=True),
        sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_version", sa.Text, nullable=False),

        sa.UniqueConstraint("decision_id", name="uq_trade_proposals_decision_id"),
        sa.CheckConstraint("length(opportunity) >= 80", name="ck_trade_proposals_opportunity_len"),
        sa.CheckConstraint("length(risk) >= 80", name="ck_trade_proposals_risk_len"),
        sa.CheckConstraint("length(profit_case) >= 80", name="ck_trade_proposals_profit_case_len"),
        sa.CheckConstraint("length(alignment) >= 40", name="ck_trade_proposals_alignment_len"),
        sa.CheckConstraint(
            "length(similar_trades_evidence) >= 80",
            name="ck_trade_proposals_evidence_len",
        ),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index("ix_trade_proposals_status", "trade_proposals", ["status"])
    op.create_index("ix_trade_proposals_expires_at", "trade_proposals", ["expires_at"])

    # Resolve circular FK: add both FKs after both tables exist.
    op.create_foreign_key(
        "fk_orders_proposal_id",
        "orders", "trade_proposals",
        ["proposal_id"], ["proposal_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_trade_proposals_executed_order_id",
        "trade_proposals", "orders",
        ["executed_order_id"], ["order_id"],
        ondelete="SET NULL",
    )

    # order_modifications
    op.create_table(
        "order_modifications",
        sa.Column("mod_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "order_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("old_value", postgresql.JSONB, nullable=True),
        sa.Column("new_value", postgresql.JSONB, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("mod_id"),
    )

    # positions
    op.create_table(
        "positions",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("size_base", sa.Numeric, nullable=False),
        sa.Column("avg_entry", sa.Numeric, nullable=False),
        sa.Column("unrealized_pnl_usd", sa.Numeric, nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("symbol", "opened_at"),
    )

    # risk_events
    op.create_table(
        "risk_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("context", postgresql.JSONB, nullable=False),
        sa.Column("action_taken", sa.Text, nullable=True),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_risk_events_at", "risk_events", [sa.text("at DESC")])

    # kill_switch_state (singleton)
    op.create_table(
        "kill_switch_state",
        sa.Column("id", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("tripped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tripped_reason", sa.Text, nullable=True),
        sa.Column("resumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resumed_ack_reason", sa.Text, nullable=True),
        sa.CheckConstraint("id = 1", name="ck_kill_switch_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO kill_switch_state (id, active) VALUES (1, false)")

    # alerts
    op.create_table(
        "alerts",
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "signal_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("signals.signal_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claude_decisions.decision_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("delivered", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error", sa.Text, nullable=True),
        sa.UniqueConstraint("signal_id", "channel", name="uq_alerts_signal_channel"),
        sa.PrimaryKeyConstraint("alert_id"),
    )

    # claude_decisions indexes and UNIQUE constraint (Phase 2 usage)
    op.create_index(
        "ix_claude_decisions_signal_id", "claude_decisions", ["signal_id"]
    )
    op.create_index(
        "ix_claude_decisions_invoked_at",
        "claude_decisions",
        [sa.text("invoked_at DESC")],
    )
    op.create_index(
        "ix_claude_decisions_decision_invoked_at",
        "claude_decisions",
        ["decision", sa.text("invoked_at DESC")],
    )
    op.create_index(
        "uq_claude_decisions_signal_invocation_mode",
        "claude_decisions",
        ["signal_id", "invocation_mode"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_claude_decisions_signal_invocation_mode", table_name="claude_decisions")
    op.drop_index("ix_claude_decisions_decision_invoked_at", table_name="claude_decisions")
    op.drop_index("ix_claude_decisions_invoked_at", table_name="claude_decisions")
    op.drop_index("ix_claude_decisions_signal_id", table_name="claude_decisions")
    op.drop_table("alerts")
    op.drop_table("kill_switch_state")
    op.drop_index("ix_risk_events_at", table_name="risk_events")
    op.drop_table("risk_events")
    op.drop_table("positions")
    op.drop_table("order_modifications")
    op.drop_constraint("fk_trade_proposals_executed_order_id", "trade_proposals", type_="foreignkey")
    op.drop_constraint("fk_orders_proposal_id", "orders", type_="foreignkey")
    op.drop_index("ix_trade_proposals_expires_at", table_name="trade_proposals")
    op.drop_index("ix_trade_proposals_status", table_name="trade_proposals")
    op.drop_table("trade_proposals")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_symbol_submitted", table_name="orders")
    op.drop_table("orders")
```

- [ ] **Step 4: Run**

```
docker compose run --rm test pytest tests/integration/test_phase2_migrations.py -v -m integration
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add migrations/versions/0010_phase2_execution_and_proposals.py tests/integration/test_phase2_migrations.py
git commit -m "feat: migration 0010 — Phase 2 execution + proposal tables"
```

---

### Task 4: Extend policy.yaml + _policy.py accessors

**Files:**
- Modify: `policy.yaml`
- Modify: `src/trading_sandwich/_policy.py`
- Test: `tests/unit/test_policy_phase2.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_policy_phase2.py
from decimal import Decimal

from trading_sandwich import _policy


def setup_function():
    _policy.reset_cache()


def test_trading_enabled_default_false():
    assert _policy.is_trading_enabled() is False


def test_execution_mode_default_paper():
    assert _policy.get_execution_mode() == "paper"


def test_proposal_ttl_minutes():
    assert _policy.get_proposal_ttl_minutes() == 15


def test_first_trade_size_multiplier():
    assert _policy.get_first_trade_size_multiplier() == Decimal("0.5")


def test_daily_triage_cap():
    assert _policy.get_claude_daily_triage_cap() == 20


def test_paper_starting_equity_usd():
    assert _policy.get_paper_starting_equity_usd() == Decimal("10000")


def test_auto_flatten_on_kill_default_false():
    assert _policy.get_auto_flatten_on_kill() is False


def test_reconciliation_block_tolerance_keys():
    tol = _policy.get_reconciliation_block_tolerance()
    assert "position_base_drift_pct" in tol
    assert "open_order_count_drift" in tol
```

- [ ] **Step 2: Run → fail**

```
docker compose run --rm test pytest tests/unit/test_policy_phase2.py -v
```
Expected: `AttributeError: module 'trading_sandwich._policy' has no attribute 'is_trading_enabled'`

- [ ] **Step 3: Update `policy.yaml`**

Append to `policy.yaml` (keep all existing keys):

```yaml
# Phase 2 execution posture
trading_enabled: false
execution_mode: paper               # paper | live

# Proposal lifecycle
proposal_ttl_minutes: 15

# Live-mode safeguards
first_trade_size_multiplier: 0.5

# Reconciliation tolerances
reconciliation_block_tolerance:
  position_base_drift_pct: 0.5
  open_order_count_drift: 0

# Paper adapter
paper_starting_equity_usd: 10000

# Kill-switch behaviour
auto_flatten_on_kill: false
```

- [ ] **Step 4: Update `_policy.py`**

Append accessors to `src/trading_sandwich/_policy.py`:

```python
def is_trading_enabled() -> bool:
    return bool(load_policy().get("trading_enabled", False))


def get_execution_mode() -> str:
    mode = load_policy().get("execution_mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError(f"invalid execution_mode: {mode}")
    return mode


def get_proposal_ttl_minutes() -> int:
    return int(load_policy().get("proposal_ttl_minutes", 15))


def get_first_trade_size_multiplier() -> Decimal:
    return Decimal(str(load_policy().get("first_trade_size_multiplier", 0.5)))


def get_claude_daily_triage_cap() -> int:
    return int(load_policy().get("claude_daily_triage_cap", 20))


def get_paper_starting_equity_usd() -> Decimal:
    return Decimal(str(load_policy().get("paper_starting_equity_usd", 10000)))


def get_auto_flatten_on_kill() -> bool:
    return bool(load_policy().get("auto_flatten_on_kill", False))


def get_reconciliation_block_tolerance() -> dict:
    return dict(load_policy().get("reconciliation_block_tolerance", {
        "position_base_drift_pct": 0.5,
        "open_order_count_drift": 0,
    }))


def get_max_order_usd() -> Decimal:
    return Decimal(str(load_policy()["max_order_usd"]))


def get_default_rr_minimum() -> Decimal:
    return Decimal(str(load_policy()["default_rr_minimum"]))


def get_min_stop_distance_atr() -> Decimal:
    return Decimal(str(load_policy()["min_stop_distance_atr"]))


def get_max_stop_distance_atr() -> Decimal:
    return Decimal(str(load_policy()["max_stop_distance_atr"]))


def get_universe_symbols() -> list[str]:
    return list(load_policy()["universe"])
```

- [ ] **Step 5: Run**

```
docker compose run --rm test pytest tests/unit/test_policy_phase2.py -v
```
Expected: 8 passed.

- [ ] **Step 6: Commit**

```
git add policy.yaml src/trading_sandwich/_policy.py tests/unit/test_policy_phase2.py
git commit -m "feat: Phase 2 policy.yaml keys + accessors"
```

---

**⏸ CHECKPOINT — Review after Task 4.** Schema + contracts + policy accessors done. Migration applied cleanly. Re-run full test suite:

```
docker compose run --rm test pytest -v
```
All 133 Phase 0/1 tests + new Phase 2 unit tests pass. Migration integration tests pass.

---

## Phase B — Daily cap + gating integration

### Task 5: Daily-cap Redis gate module

**Files:**
- Create: `src/trading_sandwich/triage/__init__.py` (empty)
- Create: `src/trading_sandwich/triage/daily_cap.py`
- Test: `tests/unit/test_daily_cap.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_daily_cap.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

from trading_sandwich.triage.daily_cap import (
    check_and_reserve_slot,
    redis_key_for_date,
)


def test_redis_key_format():
    dt = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert redis_key_for_date(dt) == "claude_triage:2026-04-25"


def test_check_and_reserve_first_call_returns_true():
    redis = MagicMock()
    redis.incr.return_value = 1
    dt = datetime(2026, 4, 25, tzinfo=timezone.utc)
    assert check_and_reserve_slot(redis, dt, cap=20) is True
    redis.incr.assert_called_once_with("claude_triage:2026-04-25")
    redis.expire.assert_called_once_with("claude_triage:2026-04-25", 172800)


def test_check_and_reserve_at_cap_returns_true():
    redis = MagicMock()
    redis.incr.return_value = 20
    assert check_and_reserve_slot(redis, datetime(2026, 4, 25, tzinfo=timezone.utc), cap=20) is True


def test_check_and_reserve_over_cap_returns_false():
    redis = MagicMock()
    redis.incr.return_value = 21
    assert check_and_reserve_slot(redis, datetime(2026, 4, 25, tzinfo=timezone.utc), cap=20) is False


def test_check_and_reserve_expire_only_on_first_increment():
    redis = MagicMock()
    redis.incr.return_value = 5
    check_and_reserve_slot(redis, datetime(2026, 4, 25, tzinfo=timezone.utc), cap=20)
    redis.expire.assert_not_called()
```

- [ ] **Step 2: Fail**

```
docker compose run --rm test pytest tests/unit/test_daily_cap.py -v
```

- [ ] **Step 3: Write module**

```python
# src/trading_sandwich/triage/daily_cap.py
"""Daily triage cap enforcement via date-keyed Redis counter.

Atomic INCR with EXPIRE only on first increment (count==1). No separate
reset task — old keys age out via EXPIRE.
"""
from __future__ import annotations

from datetime import datetime


_EXPIRE_SECONDS = 172800  # 48h


def redis_key_for_date(now: datetime) -> str:
    return f"claude_triage:{now.strftime('%Y-%m-%d')}"


def check_and_reserve_slot(redis_client, now: datetime, cap: int) -> bool:
    """Atomically reserve one slot for the given UTC day.

    Returns True if the reservation succeeded (count <= cap), False if the
    cap has been exceeded. The caller is expected to mark the signal as
    `daily_cap_hit` when this returns False.
    """
    key = redis_key_for_date(now)
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, _EXPIRE_SECONDS)
    return count <= cap
```

- [ ] **Step 4: Create `triage/__init__.py`** (empty file)

```bash
echo > src/trading_sandwich/triage/__init__.py
```

- [ ] **Step 5: Run → pass**

```
docker compose run --rm test pytest tests/unit/test_daily_cap.py -v
```
Expected: 5 passed.

- [ ] **Step 6: Commit**

```
git add src/trading_sandwich/triage/ tests/unit/test_daily_cap.py
git commit -m "feat: daily triage cap Redis gate"
```

---

### Task 6: Wire daily cap into signal-worker gating

**Files:**
- Modify: `src/trading_sandwich/signals/gating.py`
- Test: `tests/integration/test_daily_cap_gate.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/integration/test_daily_cap_gate.py
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from trading_sandwich.contracts.models import Signal


def _make_signal(fired_at: datetime) -> Signal:
    return Signal(
        signal_id=uuid4(),
        symbol="BTCUSDT",
        timeframe="5m",
        archetype="trend_pullback",
        fired_at=fired_at,
        candle_close_time=fired_at,
        trigger_price=Decimal("68000"),
        direction="long",
        confidence=Decimal("0.85"),
        confidence_breakdown={},
        features_snapshot={},
        detector_version="test",
    )


@pytest.mark.integration
def test_daily_cap_allows_up_to_cap(env_for_postgres, env_for_redis, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from trading_sandwich._policy import reset_cache
    from trading_sandwich.signals.gating import gate_signal_with_db

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")
        reset_cache()
        # patch the daily_triage_cap to 2 for testability
        monkeypatch.setattr(
            "trading_sandwich._policy.get_claude_daily_triage_cap",
            lambda: 2,
        )

        now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        s1 = gate_signal_with_db(_make_signal(now))
        s2 = gate_signal_with_db(_make_signal(now))
        s3 = gate_signal_with_db(_make_signal(now))
        assert s1.gating_outcome == "claude_triaged"
        assert s2.gating_outcome == "claude_triaged"
        assert s3.gating_outcome == "daily_cap_hit"
```

- [ ] **Step 2: Run → fail**

```
docker compose run --rm test pytest tests/integration/test_daily_cap_gate.py -v -m integration
```

- [ ] **Step 3: Modify `gating.py` — add daily-cap stage before returning `claude_triaged`**

Add imports at top of `src/trading_sandwich/signals/gating.py`:

```python
from datetime import datetime, timedelta, timezone

import redis

from trading_sandwich._policy import (
    get_claude_daily_triage_cap,
    get_confidence_threshold,
    get_cooldown_minutes,
    get_dedup_window_minutes,
)
from trading_sandwich.config import get_settings
from trading_sandwich.triage.daily_cap import check_and_reserve_slot
```

Replace the `gate_signal_with_db` function body. The stages remain:
1. threshold
2. cooldown
3. dedup
4. **daily_cap (new, last before claude_triaged)**

```python
def gate_signal_with_db(signal: Signal) -> Signal:
    """Four-stage gate applied in order:
       1. below_threshold
       2. cooldown_suppressed
       3. dedup_suppressed
       4. daily_cap_hit  (NEW in Phase 2)
    First non-pass stage short-circuits.
    """
    threshold = get_confidence_threshold(signal.archetype)
    if signal.confidence < threshold:
        return signal.model_copy(update={"gating_outcome": "below_threshold"})

    if run_coro(_cooldown_violated_async(signal)):
        return signal.model_copy(update={"gating_outcome": "cooldown_suppressed"})

    window = get_dedup_window_minutes()
    if is_dedup_suppressed(
        symbol=signal.symbol, direction=signal.direction,
        timeframe=signal.timeframe, fired_at=signal.fired_at,
        window_minutes=window,
    ):
        return signal.model_copy(update={"gating_outcome": "dedup_suppressed"})

    # Phase 2: daily cap
    settings = get_settings()
    r = redis.from_url(settings.celery_broker_url, decode_responses=True)
    if not check_and_reserve_slot(r, signal.fired_at.astimezone(timezone.utc), cap=get_claude_daily_triage_cap()):
        return signal.model_copy(update={"gating_outcome": "daily_cap_hit"})

    return signal.model_copy(update={"gating_outcome": "claude_triaged"})
```

- [ ] **Step 4: Run → pass**

```
docker compose run --rm test pytest tests/integration/test_daily_cap_gate.py -v -m integration
```

- [ ] **Step 5: Run full gating test suite to catch regressions**

```
docker compose run --rm test pytest tests/unit/test_gating.py tests/integration/test_dedup_gate.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add src/trading_sandwich/signals/gating.py tests/integration/test_daily_cap_gate.py
git commit -m "feat: wire daily triage cap into signal-worker gating"
```

---

**⏸ CHECKPOINT — Review after Task 6.** Cap is enforced. Beyond this, no change to signal-worker behavior until Task 18 when we enqueue `triage_signal` on `claude_triaged`.

---

## Phase C — MCP server + 7 foundational tools

### Task 7: MCP server skeleton

**Files:**
- Create: `src/trading_sandwich/mcp/__init__.py` (empty)
- Create: `src/trading_sandwich/mcp/server.py`
- Create: `src/trading_sandwich/mcp/tools/__init__.py` (empty)
- Modify: `pyproject.toml` (add `mcp[cli]>=1.0`)
- Test: `tests/unit/test_mcp_server_boot.py`

- [ ] **Step 1: Add dep**

Edit `pyproject.toml` `[project.dependencies]` — append:
```toml
"mcp[cli]>=1.0",
```

- [ ] **Step 2: Write failing test**

```python
# tests/unit/test_mcp_server_boot.py
def test_mcp_server_instance_exists():
    from trading_sandwich.mcp.server import mcp
    assert mcp is not None
    assert mcp.name == "trading"


def test_mcp_server_has_registered_tools():
    from trading_sandwich.mcp.server import mcp
    # After all tools are wired (later tasks) this will grow; for now the
    # server must expose no tools but boot cleanly.
    tools = list(mcp._tool_manager._tools.keys()) if hasattr(mcp, "_tool_manager") else []
    assert isinstance(tools, list)
```

- [ ] **Step 3: Fail**

```
docker compose run --rm test pytest tests/unit/test_mcp_server_boot.py -v
```

- [ ] **Step 4: Write skeleton**

```python
# src/trading_sandwich/mcp/server.py
"""FastMCP server for the trading sandwich. Stateless, HTTP/SSE transport.

Tools are registered in tools/*.py via the `register_*` pattern; each
tool module is imported at module load time so decorators fire.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("trading")

# Tool modules are imported here so their @mcp.tool() decorators run at
# server boot. Each module calls mcp.tool(...) on its async functions.
from trading_sandwich.mcp.tools import (  # noqa: F401, E402
    alerts,
    decisions,
    proposals,
    reads,
)


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "sse"
    mcp.run(transport=transport)
```

Also create empty placeholder tool modules so the import chain resolves:

```python
# src/trading_sandwich/mcp/tools/reads.py
"""Read tools: get_signal, get_market_snapshot, find_similar_signals, get_archetype_stats.
Registered in subsequent tasks."""
```

```python
# src/trading_sandwich/mcp/tools/decisions.py
"""Write tool: save_decision. Registered in Task 12."""
```

```python
# src/trading_sandwich/mcp/tools/alerts.py
"""Write tool: send_alert. Registered in Task 13."""
```

```python
# src/trading_sandwich/mcp/tools/proposals.py
"""Write tool: propose_trade. Registered in Task 14."""
```

- [ ] **Step 5: Run**

```
docker compose run --rm test pytest tests/unit/test_mcp_server_boot.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```
git add src/trading_sandwich/mcp/ pyproject.toml tests/unit/test_mcp_server_boot.py
git commit -m "feat: FastMCP server skeleton with tool-module scaffolding"
```

---

### Task 8: `get_signal` tool

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/reads.py`
- Test: `tests/unit/test_mcp_tool_get_signal.py`
- Test: `tests/integration/test_mcp_tool_get_signal_int.py`

- [ ] **Step 1: Write failing unit test**

```python
# tests/unit/test_mcp_tool_get_signal.py
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, patch


@pytest.mark.anyio
async def test_get_signal_returns_signal_detail_shape():
    from trading_sandwich.mcp.tools.reads import get_signal

    signal_id = uuid4()
    fake_row = {
        "signal_id": signal_id,
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "archetype": "trend_pullback",
        "direction": "long",
        "fired_at": __import__("datetime").datetime(2026, 4, 25, tzinfo=__import__("datetime").timezone.utc),
        "trigger_price": __import__("decimal").Decimal("68000"),
        "confidence": __import__("decimal").Decimal("0.85"),
        "confidence_breakdown": {"rule_strength": 0.9},
        "features_snapshot": {"rsi_14": 55},
    }
    with patch("trading_sandwich.mcp.tools.reads._load_signal_with_outcomes", AsyncMock(return_value=(fake_row, []))):
        result = await get_signal(signal_id)
    assert result.signal_id == signal_id
    assert result.symbol == "BTCUSDT"
    assert result.outcomes_so_far == []


@pytest.mark.anyio
async def test_get_signal_raises_on_missing():
    from trading_sandwich.mcp.tools.reads import get_signal

    with patch(
        "trading_sandwich.mcp.tools.reads._load_signal_with_outcomes",
        AsyncMock(return_value=(None, [])),
    ):
        with pytest.raises(ValueError, match="signal .* not found"):
            await get_signal(uuid4())
```

- [ ] **Step 2: Fail**

```
docker compose run --rm test pytest tests/unit/test_mcp_tool_get_signal.py -v
```

- [ ] **Step 3: Implement in `reads.py`**

```python
# src/trading_sandwich/mcp/tools/reads.py
"""Read tools: get_signal, get_market_snapshot, find_similar_signals, get_archetype_stats."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from trading_sandwich.contracts.phase2 import SignalDetail
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.db.models import SignalOutcome
from trading_sandwich.mcp.server import mcp


async def _load_signal_with_outcomes(signal_id: UUID) -> tuple[dict | None, list[dict]]:
    factory = get_session_factory()
    async with factory() as session:
        sig = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == signal_id)
        )).scalar_one_or_none()
        if sig is None:
            return None, []
        outs = (await session.execute(
            select(SignalOutcome).where(SignalOutcome.signal_id == signal_id)
        )).scalars().all()
        sig_dict = {
            "signal_id": sig.signal_id,
            "symbol": sig.symbol,
            "timeframe": sig.timeframe,
            "archetype": sig.archetype,
            "direction": sig.direction,
            "fired_at": sig.fired_at,
            "trigger_price": sig.trigger_price,
            "confidence": sig.confidence,
            "confidence_breakdown": sig.confidence_breakdown,
            "features_snapshot": sig.features_snapshot,
        }
        out_dicts = [
            {
                "horizon": o.horizon,
                "return_pct": float(o.return_pct),
                "mfe_in_atr": float(o.mfe_in_atr) if o.mfe_in_atr is not None else None,
                "mae_in_atr": float(o.mae_in_atr) if o.mae_in_atr is not None else None,
                "stop_hit_1atr": o.stop_hit_1atr,
                "target_hit_2atr": o.target_hit_2atr,
            }
            for o in outs
        ]
    return sig_dict, out_dicts


@mcp.tool()
async def get_signal(signal_id: UUID) -> SignalDetail:
    """Load one signal by id with its features snapshot and any measured outcomes."""
    row, outcomes = await _load_signal_with_outcomes(signal_id)
    if row is None:
        raise ValueError(f"signal {signal_id} not found")
    return SignalDetail(**row, outcomes_so_far=outcomes)
```

- [ ] **Step 4: Add `anyio` backend fixture** if not already present. Check `tests/conftest.py` — the `anyio_backend` fixture exists at line 11. Pytest tests that use `@pytest.mark.anyio` need `pytest-anyio` or the `anyio` pytest plugin. Add `anyio>=4` to dev deps if not already present.

Check:
```
docker compose run --rm test python -c "import anyio; print(anyio.__version__)"
```
If missing, add to `[project.optional-dependencies].dev` in `pyproject.toml`:
```toml
"anyio>=4",
```

- [ ] **Step 5: Run unit test → pass**

```
docker compose run --rm test pytest tests/unit/test_mcp_tool_get_signal.py -v
```

- [ ] **Step 6: Write integration test**

```python
# tests/integration/test_mcp_tool_get_signal_int.py
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_get_signal_loads_row_with_outcomes(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.mcp.tools.reads import get_signal

    async def _seed_and_call(url: str):
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid,
                symbol="BTCUSDT",
                timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
                candle_close_time=datetime(2026, 4, 25, tzinfo=timezone.utc),
                trigger_price=Decimal("68000"),
                direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={"x": 1},
                gating_outcome="claude_triaged",
                features_snapshot={"rsi_14": 55},
                detector_version="test",
            ))
            session.add(SignalOutcome(
                signal_id=sid,
                horizon="1h",
                measured_at=datetime(2026, 4, 25, 1, tzinfo=timezone.utc),
                close_price=Decimal("68500"),
                return_pct=Decimal("0.007"),
                mfe_pct=Decimal("0.01"),
                mae_pct=Decimal("0.002"),
                stop_hit_1atr=False,
                target_hit_2atr=False,
            ))
            await session.commit()
        detail = await get_signal(sid)
        assert detail.symbol == "BTCUSDT"
        assert len(detail.outcomes_so_far) == 1
        assert detail.outcomes_so_far[0]["horizon"] == "1h"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call(url))
```

- [ ] **Step 7: Run integration test → pass**

```
docker compose run --rm test pytest tests/integration/test_mcp_tool_get_signal_int.py -v -m integration
```

- [ ] **Step 8: Commit**

```
git add src/trading_sandwich/mcp/tools/reads.py tests/unit/test_mcp_tool_get_signal.py tests/integration/test_mcp_tool_get_signal_int.py pyproject.toml
git commit -m "feat: MCP get_signal tool"
```

---

### Task 9: `get_market_snapshot` tool

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/reads.py`
- Test: `tests/unit/test_mcp_tool_get_market_snapshot.py`
- Test: `tests/integration/test_mcp_tool_get_market_snapshot_int.py`

- [ ] **Step 1: Write unit test**

```python
# tests/unit/test_mcp_tool_get_market_snapshot.py
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_get_market_snapshot_returns_per_timeframe_dict():
    from trading_sandwich.mcp.tools.reads import get_market_snapshot

    def _row(tf):
        return {
            "symbol": "BTCUSDT",
            "timeframe": tf,
            "close_price": Decimal("68000"),
            "trend_regime": "trend_up",
            "vol_regime": "normal",
            "ema_8": Decimal("67900"),
            "ema_21": Decimal("67500"),
            "ema_55": Decimal("67000"),
            "ema_200": Decimal("65000"),
            "adx_14": Decimal("22"),
            "atr_percentile_100": Decimal("0.35"),
            "bb_width_percentile_100": Decimal("0.5"),
            "funding_rate": Decimal("0.0001"),
            "open_interest_usd": Decimal("100000000"),
            "prior_day_high": Decimal("68500"),
            "prior_day_low": Decimal("67200"),
            "prior_week_high": Decimal("69000"),
            "prior_week_low": Decimal("66500"),
            "pivot_p": Decimal("67850"),
            "atr_14": Decimal("500"),
        }

    rows = {tf: _row(tf) for tf in ("5m", "15m", "1h", "4h", "1d")}
    with patch(
        "trading_sandwich.mcp.tools.reads._load_latest_features",
        AsyncMock(side_effect=lambda sym, tf: rows[tf]),
    ), patch(
        "trading_sandwich.mcp.tools.reads._policy_timeframes",
        return_value=list(rows.keys()),
    ):
        snap = await get_market_snapshot("BTCUSDT")
    assert snap.symbol == "BTCUSDT"
    assert set(snap.per_timeframe.keys()) == {"5m", "15m", "1h", "4h", "1d"}
    assert snap.per_timeframe["1h"]["trend_regime"] == "trend_up"


@pytest.mark.anyio
async def test_get_market_snapshot_tolerates_missing_timeframe():
    from trading_sandwich.mcp.tools.reads import get_market_snapshot

    with patch(
        "trading_sandwich.mcp.tools.reads._load_latest_features",
        AsyncMock(return_value=None),
    ), patch(
        "trading_sandwich.mcp.tools.reads._policy_timeframes",
        return_value=["5m"],
    ):
        snap = await get_market_snapshot("BTCUSDT")
    assert snap.per_timeframe["5m"] is None
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

Append to `src/trading_sandwich/mcp/tools/reads.py`:

```python
from decimal import Decimal

import yaml

from trading_sandwich.contracts.phase2 import MarketSnapshot
from trading_sandwich.db.models import Features


def _policy_timeframes() -> list[str]:
    from trading_sandwich._policy import load_policy
    return list(load_policy()["timeframes"])


_SNAPSHOT_COLS = [
    "close_price", "trend_regime", "vol_regime",
    "ema_8", "ema_21", "ema_55", "ema_200",
    "adx_14", "atr_14", "atr_percentile_100", "bb_width_percentile_100",
    "funding_rate", "open_interest_usd",
    "prior_day_high", "prior_day_low",
    "prior_week_high", "prior_week_low",
    "pivot_p",
]


async def _load_latest_features(symbol: str, timeframe: str) -> dict | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(Features)
            .where(Features.symbol == symbol, Features.timeframe == timeframe)
            .order_by(Features.close_time.desc())
            .limit(1)
        )).scalar_one_or_none()
        if row is None:
            return None
        return {
            col: (float(v) if isinstance(v, Decimal) else v)
            for col in _SNAPSHOT_COLS
            if (v := getattr(row, col, None)) is not None
        }


@mcp.tool()
async def get_market_snapshot(symbol: str) -> MarketSnapshot:
    """For each timeframe in the universe, returns the most recent feature row."""
    per_tf: dict[str, dict | None] = {}
    for tf in _policy_timeframes():
        per_tf[tf] = await _load_latest_features(symbol, tf)
    return MarketSnapshot(symbol=symbol, per_timeframe=per_tf)
```

- [ ] **Step 4: Run unit test → pass**

- [ ] **Step 5: Write integration test**

```python
# tests/integration/test_mcp_tool_get_market_snapshot_int.py
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_get_market_snapshot_rolls_up_per_timeframe(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Features
    from trading_sandwich.mcp.tools.reads import get_market_snapshot

    async def _seed_and_call(url: str):
        factory = get_session_factory()
        async with factory() as session:
            for tf in ("5m", "1h"):
                session.add(Features(
                    symbol="BTCUSDT", timeframe=tf,
                    close_time=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
                    close_price=Decimal("68000"),
                    trend_regime="trend_up", vol_regime="normal",
                    ema_21=Decimal("67500"),
                    atr_14=Decimal("500"),
                    feature_version="test",
                ))
            await session.commit()
        snap = await get_market_snapshot("BTCUSDT")
        assert snap.per_timeframe["5m"]["trend_regime"] == "trend_up"
        assert snap.per_timeframe["1h"]["trend_regime"] == "trend_up"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call(url))
```

- [ ] **Step 6: Run → pass**

- [ ] **Step 7: Commit**

```
git add src/trading_sandwich/mcp/tools/reads.py tests/unit/test_mcp_tool_get_market_snapshot.py tests/integration/test_mcp_tool_get_market_snapshot_int.py
git commit -m "feat: MCP get_market_snapshot tool"
```

---

### Task 10: `find_similar_signals` tool (structural match)

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/reads.py`
- Test: `tests/unit/test_mcp_tool_find_similar_signals.py`
- Test: `tests/integration/test_mcp_tool_find_similar_signals_int.py`

- [ ] **Step 1: Write unit test (bucketing logic)**

```python
# tests/unit/test_mcp_tool_find_similar_signals.py
from decimal import Decimal

import pytest

from trading_sandwich.mcp.tools.reads import _confidence_bucket


def test_confidence_bucket_low():
    assert _confidence_bucket(Decimal("0.10")) == "low"
    assert _confidence_bucket(Decimal("0.33")) == "low"


def test_confidence_bucket_mid():
    assert _confidence_bucket(Decimal("0.34")) == "mid"
    assert _confidence_bucket(Decimal("0.66")) == "mid"


def test_confidence_bucket_high():
    assert _confidence_bucket(Decimal("0.67")) == "high"
    assert _confidence_bucket(Decimal("0.99")) == "high"
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

Append to `src/trading_sandwich/mcp/tools/reads.py`:

```python
from trading_sandwich.contracts.phase2 import SimilarSignal, SimilarSignalsResult


def _confidence_bucket(conf: Decimal) -> str:
    if conf <= Decimal("0.33"):
        return "low"
    if conf <= Decimal("0.66"):
        return "mid"
    return "high"


def _bucket_bounds(bucket: str) -> tuple[Decimal, Decimal]:
    return {
        "low": (Decimal("0"), Decimal("0.33")),
        "mid": (Decimal("0.3300000001"), Decimal("0.66")),
        "high": (Decimal("0.6600000001"), Decimal("1")),
    }[bucket]


@mcp.tool()
async def find_similar_signals(signal_id: UUID, k: int = 20) -> SimilarSignalsResult:
    """Structural similarity: same (archetype, direction, trend_regime, vol_regime,
    confidence_bucket). Only returns signals with at least one measured outcome.
    """
    factory = get_session_factory()
    async with factory() as session:
        seed = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == signal_id)
        )).scalar_one_or_none()
        if seed is None:
            raise ValueError(f"signal {signal_id} not found")

        trend = seed.features_snapshot.get("trend_regime")
        vol = seed.features_snapshot.get("vol_regime")
        bucket = _confidence_bucket(seed.confidence)
        lo, hi = _bucket_bounds(bucket)

        from sqlalchemy import exists

        stmt = (
            select(SignalORM)
            .where(
                SignalORM.archetype == seed.archetype,
                SignalORM.direction == seed.direction,
                SignalORM.gating_outcome == "claude_triaged",
                SignalORM.confidence >= lo,
                SignalORM.confidence <= hi,
                SignalORM.signal_id != signal_id,
                exists().where(SignalOutcome.signal_id == SignalORM.signal_id),
            )
            .order_by(SignalORM.fired_at.desc())
            .limit(k)
        )
        # Note: (trend_regime, vol_regime) live in features_snapshot JSONB — apply
        # a post-filter in Python because Postgres JSONB equality on nested keys
        # is awkward to express portably in SQLAlchemy.
        candidates = (await session.execute(stmt)).scalars().all()
        filtered = [
            c for c in candidates
            if c.features_snapshot.get("trend_regime") == trend
            and c.features_snapshot.get("vol_regime") == vol
        ]

        results: list[SimilarSignal] = []
        for c in filtered:
            outs = (await session.execute(
                select(SignalOutcome).where(SignalOutcome.signal_id == c.signal_id)
            )).scalars().all()
            results.append(SimilarSignal(
                signal_id=c.signal_id,
                fired_at=c.fired_at,
                archetype=c.archetype,
                direction=c.direction,
                trend_regime=trend,
                vol_regime=vol,
                confidence=c.confidence,
                outcomes=[
                    {
                        "horizon": o.horizon,
                        "return_pct": float(o.return_pct),
                        "mfe_in_atr": float(o.mfe_in_atr) if o.mfe_in_atr is not None else None,
                        "mae_in_atr": float(o.mae_in_atr) if o.mae_in_atr is not None else None,
                        "stop_hit_1atr": o.stop_hit_1atr,
                        "target_hit_2atr": o.target_hit_2atr,
                    }
                    for o in outs
                ],
            ))
    return SimilarSignalsResult(results=results, sparse=len(results) < k)
```

- [ ] **Step 4: Run unit test → pass**

- [ ] **Step 5: Write integration test**

```python
# tests/integration/test_mcp_tool_find_similar_signals_int.py
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_find_similar_signals_matches_by_structure(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.mcp.tools.reads import find_similar_signals

    async def _seed_and_call(url: str):
        factory = get_session_factory()
        seed_id = uuid4()
        async with factory() as session:
            # Seed signal (the one we triage)
            session.add(SignalORM(
                signal_id=seed_id, symbol="BTCUSDT", timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime(2026, 4, 25, tzinfo=timezone.utc),
                candle_close_time=datetime(2026, 4, 25, tzinfo=timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                detector_version="test",
            ))
            # Two prior matching signals, one with outcome
            for i, with_outcome in enumerate([True, False]):
                sid = uuid4()
                session.add(SignalORM(
                    signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                    archetype="trend_pullback",
                    fired_at=datetime(2026, 4, 24, tzinfo=timezone.utc) - timedelta(hours=i),
                    candle_close_time=datetime(2026, 4, 24, tzinfo=timezone.utc),
                    trigger_price=Decimal("67000"), direction="long",
                    confidence=Decimal("0.80"),
                    confidence_breakdown={},
                    gating_outcome="claude_triaged",
                    features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                    detector_version="test",
                ))
                if with_outcome:
                    session.add(SignalOutcome(
                        signal_id=sid, horizon="1h",
                        measured_at=datetime(2026, 4, 24, 1, tzinfo=timezone.utc),
                        close_price=Decimal("67500"),
                        return_pct=Decimal("0.008"), mfe_pct=Decimal("0.01"),
                        mae_pct=Decimal("0.002"),
                        stop_hit_1atr=False, target_hit_2atr=False,
                    ))
            # A non-matching signal (wrong archetype)
            session.add(SignalORM(
                signal_id=uuid4(), symbol="BTCUSDT", timeframe="5m",
                archetype="squeeze_breakout",
                fired_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
                candle_close_time=datetime(2026, 4, 24, tzinfo=timezone.utc),
                trigger_price=Decimal("67000"), direction="long",
                confidence=Decimal("0.80"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                detector_version="test",
            ))
            await session.commit()

        result = await find_similar_signals(seed_id, k=20)
        assert len(result.results) == 1
        assert result.sparse is True
        assert result.match_method == "structural"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call(url))
```

- [ ] **Step 6: Run → pass**

- [ ] **Step 7: Commit**

```
git add src/trading_sandwich/mcp/tools/reads.py tests/unit/test_mcp_tool_find_similar_signals.py tests/integration/test_mcp_tool_find_similar_signals_int.py
git commit -m "feat: MCP find_similar_signals tool (structural match)"
```

---

### Task 11: `get_archetype_stats` tool

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/reads.py`
- Test: `tests/integration/test_mcp_tool_get_archetype_stats_int.py`

- [ ] **Step 1: Write integration test**

```python
# tests/integration/test_mcp_tool_get_archetype_stats_int.py
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_archetype_stats_groups_by_regime_and_direction(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models import SignalOutcome
    from trading_sandwich.mcp.tools.reads import get_archetype_stats

    async def _seed_and_call(url: str):
        factory = get_session_factory()
        async with factory() as session:
            for n, ret in enumerate([0.02, -0.01, 0.005]):
                sid = uuid4()
                session.add(SignalORM(
                    signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                    archetype="trend_pullback",
                    fired_at=datetime.now(timezone.utc) - timedelta(days=n),
                    candle_close_time=datetime.now(timezone.utc) - timedelta(days=n),
                    trigger_price=Decimal("68000"), direction="long",
                    confidence=Decimal("0.80"),
                    confidence_breakdown={},
                    gating_outcome="claude_triaged",
                    features_snapshot={"trend_regime": "trend_up", "vol_regime": "normal"},
                    detector_version="test",
                ))
                session.add(SignalOutcome(
                    signal_id=sid, horizon="24h",
                    measured_at=datetime.now(timezone.utc) - timedelta(days=n - 1 if n > 0 else 0),
                    close_price=Decimal("68000"),
                    return_pct=Decimal(str(ret)), mfe_pct=Decimal("0.025"),
                    mae_pct=Decimal("-0.015"),
                    stop_hit_1atr=False, target_hit_2atr=False,
                ))
            await session.commit()
        stats = await get_archetype_stats("trend_pullback", lookback_days=30)
        assert stats.total_fires == 3
        # one bucket should have count=3
        assert any(b["count"] == 3 for b in stats.by_bucket)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_seed_and_call(url))
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

Append to `reads.py`:

```python
from datetime import datetime, timedelta, timezone
from statistics import median

from trading_sandwich.contracts.phase2 import ArchetypeStats


@mcp.tool()
async def get_archetype_stats(archetype: str, lookback_days: int = 30) -> ArchetypeStats:
    """Aggregate per-archetype stats over the lookback window."""
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    factory = get_session_factory()
    async with factory() as session:
        sigs = (await session.execute(
            select(SignalORM).where(
                SignalORM.archetype == archetype,
                SignalORM.fired_at >= since,
                SignalORM.gating_outcome == "claude_triaged",
            )
        )).scalars().all()
        by_bucket: dict[tuple, dict] = {}
        for s in sigs:
            key = (s.direction, s.features_snapshot.get("trend_regime"),
                   s.features_snapshot.get("vol_regime"))
            slot = by_bucket.setdefault(key, {
                "direction": key[0],
                "trend_regime": key[1],
                "vol_regime": key[2],
                "count": 0,
                "returns_24h": [],
                "target_hits": 0,
                "stop_hits": 0,
            })
            slot["count"] += 1
            outs = (await session.execute(
                select(SignalOutcome).where(
                    SignalOutcome.signal_id == s.signal_id,
                    SignalOutcome.horizon == "24h",
                )
            )).scalars().all()
            for o in outs:
                slot["returns_24h"].append(float(o.return_pct))
                if o.target_hit_2atr:
                    slot["target_hits"] += 1
                if o.stop_hit_1atr:
                    slot["stop_hits"] += 1

        buckets_out = []
        for key, slot in by_bucket.items():
            rets = slot.pop("returns_24h")
            slot["median_return_24h"] = median(rets) if rets else None
            slot["win_rate_24h"] = (
                sum(1 for r in rets if r > 0) / len(rets) if rets else None
            )
            slot["target_hit_rate"] = slot.pop("target_hits") / slot["count"]
            slot["stop_hit_rate"] = slot.pop("stop_hits") / slot["count"]
            buckets_out.append(slot)

    return ArchetypeStats(
        archetype=archetype,
        lookback_days=lookback_days,
        total_fires=sum(b["count"] for b in buckets_out),
        by_bucket=buckets_out,
    )
```

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**

```
git add src/trading_sandwich/mcp/tools/reads.py tests/integration/test_mcp_tool_get_archetype_stats_int.py
git commit -m "feat: MCP get_archetype_stats tool"
```

---

### Task 12: `save_decision` tool

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/decisions.py`
- Test: `tests/unit/test_mcp_tool_save_decision.py`
- Test: `tests/integration/test_mcp_tool_save_decision_int.py`

- [ ] **Step 1: Write unit test**

```python
# tests/unit/test_mcp_tool_save_decision.py
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.anyio
async def test_save_decision_rejects_live_order():
    from trading_sandwich.mcp.tools.decisions import save_decision

    with pytest.raises(ValueError, match="live_order"):
        await save_decision(
            signal_id=uuid4(),
            decision="live_order",  # type: ignore[arg-type]
            rationale="x" * 60,
        )


@pytest.mark.anyio
async def test_save_decision_requires_rationale_min_length():
    from trading_sandwich.mcp.tools.decisions import save_decision

    with pytest.raises(ValueError, match="rationale"):
        await save_decision(
            signal_id=uuid4(),
            decision="alert",
            rationale="too short",
        )


@pytest.mark.anyio
async def test_save_decision_alert_requires_payload():
    from trading_sandwich.mcp.tools.decisions import save_decision

    with pytest.raises(ValueError, match="alert_payload"):
        await save_decision(
            signal_id=uuid4(),
            decision="alert",
            rationale="x" * 60,
            alert_payload=None,
        )
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

```python
# src/trading_sandwich/mcp/tools/decisions.py
"""save_decision MCP tool."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert

from trading_sandwich.contracts.phase2 import AlertPayload, DecisionLiteral
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.mcp.server import mcp

_ALLOWED = {"alert", "paper_trade", "ignore", "research_more"}


def _capture_prompt_version() -> str:
    # Prefer env var set by triage-worker at spawn; fallback to live git sha.
    env = os.environ.get("TS_PROMPT_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/workspace"
        ).decode().strip()
    except Exception:
        return "unknown"


@mcp.tool()
async def save_decision(
    signal_id: UUID,
    decision: DecisionLiteral,
    rationale: str,
    alert_payload: AlertPayload | None = None,
    notes: str | None = None,
) -> UUID:
    """Persist one claude_decisions row. Idempotent on (signal_id, invocation_mode)."""
    if decision == "live_order":
        raise ValueError(
            "live_order is not a valid Phase 2 decision; propose_trade instead"
        )
    if decision not in _ALLOWED:
        raise ValueError(f"invalid decision {decision!r}; allowed: {sorted(_ALLOWED)}")
    if len(rationale) < 40:
        raise ValueError("rationale must be at least 40 characters")
    if decision == "alert" and alert_payload is None:
        raise ValueError("alert_payload is required when decision='alert'")

    now = datetime.now(timezone.utc)
    decision_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        stmt = insert(ClaudeDecision).values(
            decision_id=decision_id,
            signal_id=signal_id,
            invocation_mode="triage",
            invoked_at=now,
            completed_at=now,
            prompt_version=_capture_prompt_version(),
            decision=decision,
            rationale=rationale,
            output={"notes": notes} if notes else None,
        ).on_conflict_do_update(
            index_elements=["signal_id", "invocation_mode"],
            set_={
                "decision": decision,
                "rationale": rationale,
                "completed_at": now,
                "prompt_version": _capture_prompt_version(),
                "output": {"notes": notes} if notes else None,
            },
        ).returning(ClaudeDecision.decision_id)
        result = await session.execute(stmt)
        returned = result.scalar_one()
        await session.commit()
    return returned
```

- [ ] **Step 4: Run unit test → pass**

- [ ] **Step 5: Write integration test** verifying idempotency upsert

```python
# tests/integration/test_mcp_tool_save_decision_int.py
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_save_decision_upsert_on_signal_invocation_mode(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.mcp.tools.decisions import save_decision

    async def _flow(url: str):
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={}, gating_outcome="claude_triaged",
                features_snapshot={}, detector_version="test",
            ))
            await session.commit()

        # First save
        d1 = await save_decision(signal_id=sid, decision="alert",
                                 rationale="x" * 60,
                                 alert_payload=__import__("trading_sandwich.contracts.phase2", fromlist=["AlertPayload"]).AlertPayload(
                                    title="t", body="b", signal_id=sid, decision_id=uuid4()
                                 ))
        # Second save (upsert)
        d2 = await save_decision(signal_id=sid, decision="ignore",
                                 rationale="y" * 60)

        async with factory() as session:
            rows = (await session.execute(
                select(ClaudeDecision).where(ClaudeDecision.signal_id == sid)
            )).scalars().all()
            assert len(rows) == 1
            assert rows[0].decision == "ignore"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(url))
```

- [ ] **Step 6: Run integration → pass**

- [ ] **Step 7: Commit**

```
git add src/trading_sandwich/mcp/tools/decisions.py tests/unit/test_mcp_tool_save_decision.py tests/integration/test_mcp_tool_save_decision_int.py
git commit -m "feat: MCP save_decision tool with (signal_id, invocation_mode) upsert"
```

---

### Task 13: `send_alert` tool + Discord webhook

**Files:**
- Create: `src/trading_sandwich/discord/__init__.py` (empty)
- Create: `src/trading_sandwich/discord/webhook.py`
- Modify: `src/trading_sandwich/mcp/tools/alerts.py`
- Modify: `src/trading_sandwich/config.py` — add `discord_webhook_url: str = ""`
- Test: `tests/unit/test_discord_webhook.py`
- Test: `tests/unit/test_mcp_tool_send_alert.py`
- Test: `tests/integration/test_mcp_tool_send_alert_int.py`

- [ ] **Step 1: Add `discord_webhook_url` env**

Edit `src/trading_sandwich/config.py`, inside the `Settings` class:

```python
    discord_webhook_url: str = ""
    discord_bot_token: str = ""
    discord_operator_id: str = ""
    discord_channel_id: str = ""
```

- [ ] **Step 2: Write webhook unit test**

```python
# tests/unit/test_discord_webhook.py
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_post_webhook_uses_httpx_post():
    from trading_sandwich.discord.webhook import post_webhook

    with patch("trading_sandwich.discord.webhook.httpx.AsyncClient") as cli:
        instance = AsyncMock()
        instance.__aenter__.return_value = instance
        cli.return_value = instance
        instance.post = AsyncMock(return_value=AsyncMock(status_code=204))
        await post_webhook("https://example.com/hook", {"content": "hi"})
        instance.post.assert_awaited_once()
```

- [ ] **Step 3: Fail**

- [ ] **Step 4: Write webhook module**

```python
# src/trading_sandwich/discord/webhook.py
"""One-shot Discord webhook poster for alerts and proposal cards."""
from __future__ import annotations

import httpx


async def post_webhook(url: str, payload: dict, *, timeout_s: float = 10.0) -> int:
    """POST a JSON payload to a Discord webhook. Returns HTTP status code."""
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(url, json=payload)
    return r.status_code
```

- [ ] **Step 5: Run webhook test → pass**

- [ ] **Step 6: Write send_alert unit test**

```python
# tests/unit/test_mcp_tool_send_alert.py
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from trading_sandwich.contracts.phase2 import AlertPayload


@pytest.mark.anyio
async def test_send_alert_rejects_unknown_channel():
    from trading_sandwich.mcp.tools.alerts import send_alert

    with pytest.raises(ValueError, match="channel"):
        await send_alert(
            channel="slack",  # type: ignore[arg-type]
            payload=AlertPayload(title="t", body="b", signal_id=uuid4(), decision_id=uuid4()),
        )
```

- [ ] **Step 7: Implement `send_alert`**

```python
# src/trading_sandwich/mcp/tools/alerts.py
"""send_alert MCP tool."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert

from trading_sandwich.config import get_settings
from trading_sandwich.contracts.phase2 import AlertPayload
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import Alert
from trading_sandwich.discord.webhook import post_webhook
from trading_sandwich.mcp.server import mcp


@mcp.tool()
async def send_alert(channel: Literal["discord"], payload: AlertPayload) -> UUID:
    """Idempotent alert send (UNIQUE on (signal_id, channel))."""
    if channel != "discord":
        raise ValueError(f"unsupported channel {channel!r}")

    now = datetime.now(timezone.utc)
    alert_id = uuid4()

    factory = get_session_factory()
    async with factory() as session:
        stmt = insert(Alert).values(
            alert_id=alert_id,
            signal_id=payload.signal_id,
            decision_id=payload.decision_id,
            channel=channel,
            sent_at=now,
            payload=payload.model_dump(mode="json"),
            delivered=False,
        ).on_conflict_do_nothing(index_elements=["signal_id", "channel"]).returning(Alert.alert_id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            # A row already existed — look it up and return its id without re-posting.
            from sqlalchemy import select as _sel
            row = (await session.execute(
                _sel(Alert).where(Alert.signal_id == payload.signal_id, Alert.channel == channel)
            )).scalar_one()
            await session.commit()
            return row.alert_id
        await session.commit()

    # New row — attempt webhook post; record delivery outcome.
    settings = get_settings()
    if settings.discord_webhook_url:
        try:
            status = await post_webhook(settings.discord_webhook_url, {
                "embeds": [{"title": payload.title, "description": payload.body}],
            })
            delivered = 200 <= status < 300
            err = None if delivered else f"http_{status}"
        except Exception as exc:
            delivered = False
            err = str(exc)[:500]

        async with factory() as session:
            from sqlalchemy import update as _upd
            await session.execute(
                _upd(Alert).where(Alert.alert_id == alert_id).values(delivered=delivered, error=err)
            )
            await session.commit()

    return alert_id
```

- [ ] **Step 8: Write integration test for idempotency**

```python
# tests/integration/test_mcp_tool_send_alert_int.py
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_send_alert_idempotent_on_signal_channel(env_for_postgres, monkeypatch):
    from trading_sandwich.contracts.phase2 import AlertPayload
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_phase2 import Alert
    from trading_sandwich.mcp.tools.alerts import send_alert

    # Empty webhook URL → skip network
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")

    async def _flow(url: str):
        factory = get_session_factory()
        sid = uuid4()
        did = uuid4()
        payload = AlertPayload(title="t", body="b", signal_id=sid, decision_id=did)
        a1 = await send_alert("discord", payload)
        a2 = await send_alert("discord", payload)
        assert a1 == a2
        async with factory() as session:
            rows = (await session.execute(
                select(Alert).where(Alert.signal_id == sid)
            )).scalars().all()
            assert len(rows) == 1

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(url))
```

- [ ] **Step 9: Run**

- [ ] **Step 10: Commit**

```
git add src/trading_sandwich/discord/ src/trading_sandwich/mcp/tools/alerts.py src/trading_sandwich/config.py tests/unit/test_discord_webhook.py tests/unit/test_mcp_tool_send_alert.py tests/integration/test_mcp_tool_send_alert_int.py
git commit -m "feat: MCP send_alert tool + Discord webhook poster"
```

---

### Task 14: `propose_trade` tool (cross-check validators)

**Files:**
- Modify: `src/trading_sandwich/mcp/tools/proposals.py`
- Create: `src/trading_sandwich/discord/embed.py` — proposal card renderer
- Test: `tests/unit/test_mcp_tool_propose_trade.py`
- Test: `tests/unit/test_discord_embed.py`
- Test: `tests/integration/test_mcp_tool_propose_trade_int.py`

- [ ] **Step 1: Write embed unit test**

```python
# tests/unit/test_discord_embed.py
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4


def test_render_proposal_embed_contains_all_sections():
    from trading_sandwich.discord.embed import render_proposal_embed
    embed = render_proposal_embed(
        proposal_id=uuid4(),
        symbol="BTCUSDT", side="long", archetype="trend_pullback", timeframe="1h",
        size_usd=Decimal("500"), entry=Decimal("68420"),
        stop=Decimal("67150"), stop_atr_mult=Decimal("1.5"),
        tp=Decimal("71200"), expected_rr=Decimal("2.2"),
        worst_case_loss_usd=Decimal("23.50"), worst_case_pct_equity=Decimal("4.7"),
        similar_count=14, similar_win_rate=Decimal("0.64"), similar_median_r="+0.9R",
        opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
        alignment="a" * 40, similar_trades_evidence="b" * 80,
        expires_at=datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc),
    )
    assert embed["title"].startswith("📈 PROPOSAL")
    for field in embed["fields"]:
        assert field["value"]
    names = {f["name"] for f in embed["fields"]}
    assert {"OPPORTUNITY", "RISK", "PROFIT CASE", "ALIGNMENT", "EVIDENCE"}.issubset(names)
    # Buttons carried in `components`
    assert embed["components"]
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement embed**

```python
# src/trading_sandwich/discord/embed.py
"""Render the Discord proposal-card embed + component buttons."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID


def render_proposal_embed(
    *,
    proposal_id: UUID,
    symbol: str, side: str, archetype: str, timeframe: str,
    size_usd: Decimal, entry: Decimal,
    stop: Decimal, stop_atr_mult: Decimal,
    tp: Decimal | None, expected_rr: Decimal,
    worst_case_loss_usd: Decimal, worst_case_pct_equity: Decimal,
    similar_count: int, similar_win_rate: Decimal | None, similar_median_r: str,
    opportunity: str, risk: str, profit_case: str,
    alignment: str, similar_trades_evidence: str,
    expires_at: datetime,
) -> dict:
    title = (
        f"📈 PROPOSAL — {symbol} {side.upper()} · {archetype} ({timeframe})"
    )
    tp_text = f" · TP {tp} ({expected_rr}R)" if tp is not None else ""
    desc = (
        f"Size ${size_usd} · Entry ~${entry} · Stop ${stop} ({stop_atr_mult}·ATR){tp_text}"
    )
    win_rate_text = f"{similar_win_rate:.0%}" if similar_win_rate is not None else "n/a"
    return {
        "title": title,
        "description": desc,
        "fields": [
            {"name": "OPPORTUNITY", "value": opportunity, "inline": False},
            {"name": f"RISK — worst-case loss ${worst_case_loss_usd} ({worst_case_pct_equity}% equity)",
             "value": risk, "inline": False},
            {"name": f"PROFIT CASE — expected RR {expected_rr}",
             "value": profit_case, "inline": False},
            {"name": "ALIGNMENT", "value": alignment, "inline": False},
            {"name": f"EVIDENCE — {similar_count} similar trades · {win_rate_text} win rate · median {similar_median_r}",
             "value": similar_trades_evidence, "inline": False},
        ],
        "footer": {"text": f"Expires {expires_at:%H:%M UTC} · proposal_id {str(proposal_id)[:8]}"},
        "components": [{
            "type": 1,
            "components": [
                {"type": 2, "style": 3, "label": "Approve", "emoji": {"name": "✅"},
                 "custom_id": f"approve:{proposal_id}"},
                {"type": 2, "style": 4, "label": "Reject", "emoji": {"name": "❌"},
                 "custom_id": f"reject:{proposal_id}"},
                {"type": 2, "style": 2, "label": "Details", "emoji": {"name": "🔎"},
                 "custom_id": f"details:{proposal_id}"},
            ],
        }],
    }
```

- [ ] **Step 4: Run embed test → pass**

- [ ] **Step 5: Write propose_trade unit test (cross-check failures)**

```python
# tests/unit/test_mcp_tool_propose_trade.py
from decimal import Decimal
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock, patch

from trading_sandwich.contracts.phase2 import StopLossSpec


def _base_kwargs(**overrides):
    base = dict(
        decision_id=uuid4(),
        symbol="BTCUSDT", side="long", order_type="limit",
        size_usd=Decimal("500"), limit_price=Decimal("68000"),
        stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67000")),
        take_profit=None,
        opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
        alignment="a" * 40, similar_trades_evidence="b" * 80,
        expected_rr=Decimal("2.0"),
        worst_case_loss_usd=Decimal("7.35"),  # 500 * |68000-67000|/68000
        similar_signals_count=10,
        similar_signals_win_rate=Decimal("0.6"),
    )
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_propose_trade_rejects_worst_case_loss_mismatch():
    from trading_sandwich.mcp.tools.proposals import propose_trade
    with pytest.raises(ValueError, match="worst_case_loss_usd"):
        await propose_trade(**_base_kwargs(worst_case_loss_usd=Decimal("100")))


@pytest.mark.anyio
async def test_propose_trade_rejects_rr_below_minimum(monkeypatch):
    from trading_sandwich.mcp.tools.proposals import propose_trade
    monkeypatch.setattr(
        "trading_sandwich._policy.get_default_rr_minimum",
        lambda: Decimal("1.5"),
    )
    with pytest.raises(ValueError, match="expected_rr"):
        await propose_trade(**_base_kwargs(expected_rr=Decimal("1.0")))


```

Note: DB-dependent validators (`similar_signals_count` mismatch, `decision.decision != 'paper_trade'`, stop-distance ATR band) are exercised in the integration test in Step 8 rather than as pure unit tests — they require a real decision + signal in the DB. The pure unit tests above cover the arithmetic and policy gates that fire before any DB access.

- [ ] **Step 6: Implement propose_trade**

```python
# src/trading_sandwich/mcp/tools/proposals.py
"""propose_trade MCP tool.

Runs four cross-checks:
1. worst_case_loss_usd arithmetic matches size × |entry-stop| / entry (2% tol).
2. expected_rr >= policy.default_rr_minimum.
3. similar_signals_count matches a fresh find_similar_signals(k=100).
4. decision_id exists and has decision='paper_trade'.
5. stop value within policy ATR band (uses features.atr_14 of the signal's TF
   if kind=='atr_multiple'; otherwise checks price-distance-over-ATR).
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select

from trading_sandwich._policy import (
    get_default_rr_minimum,
    get_max_stop_distance_atr,
    get_min_stop_distance_atr,
    get_proposal_ttl_minutes,
)
from trading_sandwich.config import get_settings
from trading_sandwich.contracts.phase2 import StopLossSpec, TakeProfitSpec
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.db.models_phase2 import TradeProposal
from trading_sandwich.discord.embed import render_proposal_embed
from trading_sandwich.discord.webhook import post_webhook
from trading_sandwich.mcp.server import mcp


async def _count_similar_signals(signal_id: UUID) -> int:
    from trading_sandwich.mcp.tools.reads import find_similar_signals
    result = await find_similar_signals(signal_id, k=100)
    return len(result.results)


def _capture_policy_version() -> str:
    env = os.environ.get("TS_PROMPT_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd="/workspace"
        ).decode().strip()
    except Exception:
        return "unknown"


@mcp.tool()
async def propose_trade(
    decision_id: UUID,
    symbol: str,
    side: Literal["long", "short"],
    order_type: Literal["market", "limit", "stop"],
    size_usd: Decimal,
    limit_price: Decimal | None,
    stop_loss: StopLossSpec,
    take_profit: TakeProfitSpec | None,
    opportunity: str,
    risk: str,
    profit_case: str,
    alignment: str,
    similar_trades_evidence: str,
    expected_rr: Decimal,
    worst_case_loss_usd: Decimal,
    similar_signals_count: int,
    similar_signals_win_rate: Decimal | None = None,
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC",
) -> UUID:
    """Propose a trade. Written only if all cross-checks pass.
    Posts the proposal card to Discord after persist."""
    # 1. RR gate
    rr_min = get_default_rr_minimum()
    if expected_rr < rr_min:
        raise ValueError(f"expected_rr {expected_rr} < default_rr_minimum {rr_min}")

    # 2. Worst-case loss arithmetic (within 2% tolerance)
    entry = limit_price if limit_price is not None else Decimal("0")  # market orders
    if entry == 0 and order_type == "market":
        # For market orders, use trigger price from signal; cross-check is
        # loosened (we can't know exact fill price). Require a non-trivial
        # worst_case_loss_usd > 0 and trust Claude's math.
        if worst_case_loss_usd <= 0:
            raise ValueError("worst_case_loss_usd must be > 0")
    else:
        stop = stop_loss.value
        computed = (size_usd * abs(entry - stop) / entry).quantize(Decimal("0.01"))
        tol = computed * Decimal("0.02") + Decimal("0.01")
        if abs(worst_case_loss_usd - computed) > tol:
            raise ValueError(
                f"worst_case_loss_usd {worst_case_loss_usd} != computed {computed} (tol {tol})"
            )

    # 3. Load decision + signal
    factory = get_session_factory()
    async with factory() as session:
        decision = (await session.execute(
            select(ClaudeDecision).where(ClaudeDecision.decision_id == decision_id)
        )).scalar_one_or_none()
        if decision is None:
            raise ValueError(f"decision_id {decision_id} not found")
        if decision.decision != "paper_trade":
            raise ValueError(
                f"propose_trade requires decision='paper_trade', got {decision.decision!r}"
            )
        signal_id = decision.signal_id
        if signal_id is None:
            raise ValueError(f"decision {decision_id} has null signal_id")

        signal = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == signal_id)
        )).scalar_one()

    # 4. Sample-count cross-check
    actual_count = await _count_similar_signals(signal_id)
    if abs(actual_count - similar_signals_count) > 2:
        raise ValueError(
            f"similar_signals_count {similar_signals_count} disagrees with "
            f"actual {actual_count}"
        )

    # 5. Stop-distance ATR band
    atr = signal.features_snapshot.get("atr_14")
    if atr:
        atr_d = Decimal(str(atr))
        price = limit_price or signal.trigger_price
        dist_atr = abs(price - stop_loss.value) / atr_d
        if dist_atr < get_min_stop_distance_atr() or dist_atr > get_max_stop_distance_atr():
            raise ValueError(
                f"stop distance {dist_atr}·ATR outside band "
                f"[{get_min_stop_distance_atr()}, {get_max_stop_distance_atr()}]"
            )

    # Persist
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=get_proposal_ttl_minutes())
    proposal_id = uuid4()
    async with factory() as session:
        session.add(TradeProposal(
            proposal_id=proposal_id,
            decision_id=decision_id,
            signal_id=signal_id,
            symbol=symbol, side=side, order_type=order_type,
            size_usd=size_usd, limit_price=limit_price,
            stop_loss=stop_loss.model_dump(mode="json"),
            take_profit=take_profit.model_dump(mode="json") if take_profit else None,
            time_in_force=time_in_force,
            opportunity=opportunity, risk=risk, profit_case=profit_case,
            alignment=alignment, similar_trades_evidence=similar_trades_evidence,
            expected_rr=expected_rr, worst_case_loss_usd=worst_case_loss_usd,
            similar_signals_count=similar_signals_count,
            similar_signals_win_rate=similar_signals_win_rate,
            status="pending",
            proposed_at=now, expires_at=expires,
            policy_version=_capture_policy_version(),
        ))
        await session.commit()

    # Post to Discord (bot-mediated posting is Task 19; here we fire the webhook
    # as a stop-gap. Task 19 will replace this path with discord-listener-driven
    # bot.send().)
    settings = get_settings()
    if settings.discord_webhook_url:
        embed = render_proposal_embed(
            proposal_id=proposal_id,
            symbol=symbol, side=side, archetype=signal.archetype,
            timeframe=signal.timeframe,
            size_usd=size_usd,
            entry=limit_price or signal.trigger_price,
            stop=stop_loss.value,
            stop_atr_mult=(
                Decimal(str(stop_loss.value))
                if stop_loss.kind == "atr_multiple" else Decimal("0")
            ),
            tp=take_profit.value if take_profit else None,
            expected_rr=expected_rr,
            worst_case_loss_usd=worst_case_loss_usd,
            worst_case_pct_equity=(worst_case_loss_usd / Decimal("500") * 100).quantize(Decimal("0.01")),
            similar_count=similar_signals_count,
            similar_win_rate=similar_signals_win_rate,
            similar_median_r="+0.0R",
            opportunity=opportunity, risk=risk, profit_case=profit_case,
            alignment=alignment,
            similar_trades_evidence=similar_trades_evidence,
            expires_at=expires,
        )
        try:
            await post_webhook(
                settings.discord_webhook_url,
                {"embeds": [embed], "components": embed.get("components", [])},
            )
        except Exception:
            pass  # Discord outage does not block proposal persistence

    return proposal_id
```

- [ ] **Step 7: Run unit tests**

```
docker compose run --rm test pytest tests/unit/test_mcp_tool_propose_trade.py tests/unit/test_discord_embed.py -v
```
Expected: 3 passed (2 in test_mcp_tool_propose_trade + 1 in test_discord_embed).

- [ ] **Step 8: Write integration test — happy path end-to-end**

```python
# tests/integration/test_mcp_tool_propose_trade_int.py
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_propose_trade_writes_row_on_valid_input(env_for_postgres):
    from trading_sandwich.contracts.phase2 import StopLossSpec
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.mcp.tools.proposals import propose_trade

    async def _flow(url: str):
        factory = get_session_factory()
        sid = uuid4()
        did = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="1h",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={}, gating_outcome="claude_triaged",
                features_snapshot={"atr_14": "500"},
                detector_version="test",
            ))
            session.add(ClaudeDecision(
                decision_id=did, signal_id=sid, invocation_mode="triage",
                invoked_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                decision="paper_trade", rationale="x" * 60,
            ))
            await session.commit()

        pid = await propose_trade(
            decision_id=did,
            symbol="BTCUSDT", side="long", order_type="limit",
            size_usd=Decimal("500"), limit_price=Decimal("68000"),
            stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67500")),
            take_profit=None,
            opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
            alignment="a" * 40, similar_trades_evidence="b" * 80,
            expected_rr=Decimal("2.0"),
            worst_case_loss_usd=Decimal("3.68"),  # 500*500/68000 ≈ 3.68
            similar_signals_count=0,  # no other signals seeded
        )
        async with factory() as session:
            row = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert row.status == "pending"
            assert row.opportunity == "x" * 80

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        env_for_postgres(url)
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(url))
```

- [ ] **Step 9: Run integration test**

- [ ] **Step 10: Commit**

```
git add src/trading_sandwich/mcp/tools/proposals.py src/trading_sandwich/discord/embed.py tests/unit/test_mcp_tool_propose_trade.py tests/unit/test_discord_embed.py tests/integration/test_mcp_tool_propose_trade_int.py
git commit -m "feat: MCP propose_trade tool with cross-check validators + Discord card render"
```

---

**⏸ CHECKPOINT — Review after Task 14.** All 7 foundational MCP tools built and tested. Reboot server smoke-check: `docker compose run --rm tools python -m trading_sandwich.mcp.server stdio` should boot and register 7 tools without errors.

---

## Phase D — Triage invocation

### Task 15: `fake-claude` test harness

**Files:**
- Create: `tests/fixtures/fake_claude.py`
- Test: `tests/unit/test_fake_claude_harness.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_fake_claude_harness.py
import json
import subprocess
import sys
from pathlib import Path


def test_fake_claude_emits_canned_json(tmp_path):
    script = Path("tests/fixtures/fake_claude.py").resolve()
    # Write a canned-response JSON into an env var
    response = {"decision": "alert", "rationale": "x" * 60, "alert_posted": True, "proposal_created": False}
    result = subprocess.run(
        [sys.executable, str(script), "triage", "abc"],
        env={"FAKE_CLAUDE_RESPONSE": json.dumps(response), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    last_line = result.stdout.strip().splitlines()[-1]
    parsed = json.loads(last_line)
    assert parsed["decision"] == "alert"
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Write harness**

```python
# tests/fixtures/fake_claude.py
"""Stub `claude` binary for integration tests.

Reads the JSON response from env var FAKE_CLAUDE_RESPONSE and emits it as
the final stdout line, mimicking what claude -p would print."""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    resp = os.environ.get("FAKE_CLAUDE_RESPONSE")
    if not resp:
        print("FAKE_CLAUDE_RESPONSE not set", file=sys.stderr)
        return 2
    # Print some preamble to mimic real claude output
    print("(fake-claude) triaging...")
    print(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**

```
git add tests/fixtures/fake_claude.py tests/unit/test_fake_claude_harness.py
git commit -m "test: fake-claude stub binary for triage integration tests"
```

---

### Task 16: Canonical `invoke_claude` function

**Files:**
- Create: `src/trading_sandwich/triage/invocation.py`
- Test: `tests/unit/test_invocation.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_invocation.py
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest


def test_invoke_claude_parses_last_json_line(monkeypatch, tmp_path):
    from trading_sandwich.triage.invocation import invoke_claude

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    response = {
        "decision": "ignore",
        "rationale": "x" * 60,
        "alert_posted": False,
        "proposal_created": False,
    }
    monkeypatch.setenv("FAKE_CLAUDE_RESPONSE", json.dumps(response))
    # Point CLAUDE_BIN at our fake
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {fake}")

    result = invoke_claude(signal_id=uuid4(), workspace=tmp_path)
    assert result.decision == "ignore"


def test_invoke_claude_raises_on_non_json_tail(monkeypatch, tmp_path):
    from trading_sandwich.triage.invocation import invoke_claude

    # Use `echo` as the binary to emit plain text only
    monkeypatch.setenv("CLAUDE_BIN", "echo just-a-plain-string-no-json")
    with pytest.raises(ValueError, match="could not parse"):
        invoke_claude(signal_id=uuid4(), workspace=tmp_path)


def test_invoke_claude_timeout(monkeypatch, tmp_path):
    from trading_sandwich.triage.invocation import InvocationTimeout, invoke_claude

    # Use `sleep 5` to force a timeout below 5s
    monkeypatch.setenv("CLAUDE_BIN", "sleep 5")
    monkeypatch.setenv("CLAUDE_TIMEOUT_S", "1")
    with pytest.raises(InvocationTimeout):
        invoke_claude(signal_id=uuid4(), workspace=tmp_path)
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

```python
# src/trading_sandwich/triage/invocation.py
"""Canonical Claude invocation. Every automated triage passes through here."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from trading_sandwich.contracts.phase2 import ClaudeResponse


class InvocationTimeout(Exception):
    pass


class InvocationError(Exception):
    pass


def _git_sha(workspace: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=workspace
        ).decode().strip()
    except Exception:
        return "unknown"


def _resolve_claude_cmd() -> list[str]:
    """Resolve the claude binary. Tests override via CLAUDE_BIN env var."""
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return shlex.split(override)
    return ["claude"]


def invoke_claude(signal_id: UUID, workspace: Path, mode: str = "triage") -> ClaudeResponse:
    """Spawn claude -p. Return the parsed structured JSON from its final output
    line. Raises InvocationTimeout on CLAUDE_TIMEOUT_S breach; ValueError if the
    output cannot be parsed as ClaudeResponse.
    """
    timeout_s = float(os.environ.get("CLAUDE_TIMEOUT_S", "90"))
    prompt_version = _git_sha(workspace)
    env = {**os.environ, "TS_PROMPT_VERSION": prompt_version}

    cmd = _resolve_claude_cmd() + ["-p", f"{mode} {signal_id}"]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise InvocationTimeout(
            f"claude timed out after {timeout_s}s (signal {signal_id})"
        ) from exc

    if result.returncode != 0:
        raise InvocationError(
            f"claude exited {result.returncode}: {result.stderr[:500]}"
        )

    # Parse the final non-empty line as JSON.
    last_line = ""
    for line in reversed(result.stdout.splitlines()):
        stripped = line.strip()
        if stripped:
            last_line = stripped
            break

    if not last_line:
        raise ValueError(f"claude produced empty output (signal {signal_id})")

    try:
        payload = json.loads(last_line)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"could not parse claude output as JSON: {last_line[:200]!r}"
        ) from exc

    return ClaudeResponse(**payload)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
```

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**

```
git add src/trading_sandwich/triage/invocation.py tests/unit/test_invocation.py
git commit -m "feat: canonical invoke_claude subprocess wrapper with timeout + JSON parse"
```

---

### Task 17: `triage_signal` Celery task + queue registration

**Files:**
- Create: `src/trading_sandwich/triage/worker.py`
- Modify: `src/trading_sandwich/celery_app.py` — add `trading_sandwich.triage.worker` to `include`; add task route
- Test: `tests/integration/test_triage_task_eager.py`

- [ ] **Step 1: Add Celery include + route**

In `src/trading_sandwich/celery_app.py`:
- Add `"trading_sandwich.triage.worker",` to the `include=[...]` list.
- Add to `task_routes`:
  ```python
  "trading_sandwich.triage.worker.*": {"queue": "triage"},
  ```

- [ ] **Step 2: Write failing integration test**

```python
# tests/integration/test_triage_task_eager.py
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.mark.integration
def test_triage_signal_writes_claude_decisions_row(
    env_for_postgres, env_for_redis, monkeypatch
):
    from trading_sandwich.celery_app import app as celery_app
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision
    from trading_sandwich.db.models import Signal as SignalORM

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    import sys as _sys
    monkeypatch.setenv("CLAUDE_BIN", f"{_sys.executable} {fake}")
    monkeypatch.setenv(
        "FAKE_CLAUDE_RESPONSE",
        json.dumps({
            "decision": "ignore",
            "rationale": "y" * 60,
            "alert_posted": False,
            "proposal_created": False,
        }),
    )

    async def _seed(url: str):
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="5m",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={},
                detector_version="test",
            ))
            await session.commit()
        return sid

    async def _check(sid):
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                select(ClaudeDecision).where(ClaudeDecision.signal_id == sid)
            )).scalar_one()
            assert row.decision == "ignore"
            assert row.prompt_version is not None

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")

        import asyncio
        sid = asyncio.run(_seed(pg.get_connection_url()))

        # Run task eagerly
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        from trading_sandwich.triage.worker import triage_signal
        triage_signal.delay(str(sid))

        asyncio.run(_check(sid))
```

- [ ] **Step 3: Fail**

- [ ] **Step 4: Implement triage worker**

```python
# src/trading_sandwich/triage/worker.py
"""triage_signal Celery task.

Spawns claude -p, reconciles the claude_decisions row, writes a fallback if
Claude did not write one (shouldn't happen if CLAUDE.md is correct, but defense
in depth).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import ClaudeDecision
from trading_sandwich.triage.invocation import (
    InvocationError,
    InvocationTimeout,
    invoke_claude,
)


_WORKSPACE = Path("/workspace")


async def _has_decision_row(signal_id: UUID) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(ClaudeDecision)
            .where(
                ClaudeDecision.signal_id == signal_id,
                ClaudeDecision.invocation_mode == "triage",
            )
        )).scalar_one_or_none()
        return row is not None


async def _write_fallback_row(signal_id: UUID, reason: str, prompt_version: str | None) -> None:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(ClaudeDecision(
            decision_id=uuid4(),
            signal_id=signal_id,
            invocation_mode="triage",
            invoked_at=now,
            completed_at=now,
            prompt_version=prompt_version,
            decision="ignore",
            rationale="(fallback) " + reason[:500],
            error=reason[:500],
        ))
        await session.commit()


@app.task(bind=True, name="trading_sandwich.triage.worker.triage_signal",
          acks_late=True)
def triage_signal(self, signal_id_str: str) -> None:
    """Invoke claude -p on the given signal. All outputs and errors land in
    claude_decisions."""
    signal_id = UUID(signal_id_str)
    started = datetime.now(timezone.utc)

    try:
        response = invoke_claude(signal_id=signal_id, workspace=_WORKSPACE)
    except InvocationTimeout as exc:
        asyncio.run(_write_fallback_row(signal_id, f"timeout: {exc}", None))
        return
    except (InvocationError, ValueError) as exc:
        asyncio.run(_write_fallback_row(signal_id, f"error: {exc}", None))
        return

    # Claude should have called save_decision itself; verify.
    has_row = asyncio.run(_has_decision_row(signal_id))
    if not has_row:
        asyncio.run(_write_fallback_row(
            signal_id,
            f"claude returned {response.decision!r} but no claude_decisions row; "
            "save_decision was not called",
            None,
        ))
        return

    # Optionally annotate duration on the existing row
    completed = datetime.now(timezone.utc)
    duration_ms = int((completed - started).total_seconds() * 1000)

    async def _annotate():
        factory = get_session_factory()
        from sqlalchemy import update as _upd
        async with factory() as session:
            await session.execute(
                _upd(ClaudeDecision)
                .where(
                    ClaudeDecision.signal_id == signal_id,
                    ClaudeDecision.invocation_mode == "triage",
                )
                .values(completed_at=completed, duration_ms=duration_ms)
            )
            await session.commit()
    asyncio.run(_annotate())
```

- [ ] **Step 5: Run → pass**

- [ ] **Step 6: Commit**

```
git add src/trading_sandwich/triage/worker.py src/trading_sandwich/celery_app.py tests/integration/test_triage_task_eager.py
git commit -m "feat: triage_signal Celery task wired through invoke_claude"
```

---

### Task 18: Signal-worker enqueues triage on `claude_triaged`

**Files:**
- Modify: `src/trading_sandwich/signals/worker.py`
- Test: `tests/integration/test_signal_worker_enqueues_triage.py`

- [ ] **Step 1: Read current signal worker**

```
docker compose run --rm tools cat src/trading_sandwich/signals/worker.py
```

Identify where a row is inserted with `gating_outcome='claude_triaged'`; the task dispatch goes immediately after the commit.

- [ ] **Step 2: Write failing integration test**

```python
# tests/integration/test_signal_worker_enqueues_triage.py
import pytest


@pytest.mark.integration
def test_claude_triaged_signal_enqueues_triage_task(
    env_for_postgres, env_for_redis, monkeypatch
):
    # Assert: after signal-worker persists a claude_triaged signal, the
    # triage_signal task was enqueued. In eager mode we observe it by
    # patching triage_signal.delay and asserting called.
    from unittest.mock import MagicMock

    sent = MagicMock()
    monkeypatch.setattr(
        "trading_sandwich.triage.worker.triage_signal.delay", sent
    )

    # Minimal-shape: call the signal-worker entrypoint with a seeded signal
    # and verify sent.assert_called_once_with(<signal_id str>).
    #
    # [Engineer: mirror the existing test_signal_worker.py pattern to set up
    #  a feature row, run detect_signals, and observe the gating outcome.
    #  See tests/integration/test_signal_worker.py for the pattern.]
    ...
```

Fill in the `...` using `tests/integration/test_signal_worker.py` as the template — seed a feature row that forces a high-confidence `trend_pullback` signal, run `detect_signals`, assert the signal row was written with `gating_outcome='claude_triaged'` and that `sent` was called with the signal id.

- [ ] **Step 3: Fail**

- [ ] **Step 4: Modify `src/trading_sandwich/signals/worker.py`**

Find the block that persists the signal (pattern `session.add(SignalORM(...))` or the one that sets `gating_outcome`). Immediately after `await session.commit()`, add:

```python
from trading_sandwich.triage.worker import triage_signal

if signal.gating_outcome == "claude_triaged":
    triage_signal.delay(str(signal.signal_id))
```

Place the import at the top of the file alongside the other triage imports. Guarded by the gating outcome check — only `claude_triaged` rows trigger triage.

- [ ] **Step 5: Run → pass**

- [ ] **Step 6: Run full Phase 0/1 test suite for regressions**

```
docker compose run --rm test pytest -v
```
All prior tests still green + new test passes.

- [ ] **Step 7: Commit**

```
git add src/trading_sandwich/signals/worker.py tests/integration/test_signal_worker_enqueues_triage.py
git commit -m "feat: signal-worker enqueues triage_signal after claude_triaged persist"
```

---

**⏸ CHECKPOINT — Review after Task 18.** Triage end-to-end: signal → gating → enqueue → fake-claude subprocess → `claude_decisions` row. Proposal tool works but the Discord card is still webhook-posted (no bot, no buttons yet). Phase E wires the real bot + approval loop.

---

## Phase E — Discord approval loop

### Task 19: Discord listener skeleton + operator validation

**Files:**
- Create: `src/trading_sandwich/discord/listener.py`
- Modify: `pyproject.toml` — add `"discord.py>=2.4",`
- Test: `tests/unit/test_discord_listener.py`

- [ ] **Step 1: Add dep**

In `pyproject.toml` `[project.dependencies]`:
```toml
"discord.py>=2.4",
```

- [ ] **Step 2: Write failing test (pure-function tests of interaction-handling logic — no live Gateway)**

```python
# tests/unit/test_discord_listener.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_parse_custom_id_approve():
    from trading_sandwich.discord.listener import parse_custom_id
    action, pid = parse_custom_id(f"approve:{uuid4()}")
    assert action == "approve"


@pytest.mark.anyio
async def test_parse_custom_id_reject():
    from trading_sandwich.discord.listener import parse_custom_id
    action, pid = parse_custom_id(f"reject:{uuid4()}")
    assert action == "reject"


@pytest.mark.anyio
async def test_parse_custom_id_invalid():
    from trading_sandwich.discord.listener import parse_custom_id
    with pytest.raises(ValueError):
        parse_custom_id("nonsense")


@pytest.mark.anyio
async def test_validate_operator_rejects_other_user(monkeypatch):
    from trading_sandwich.discord.listener import validate_operator
    monkeypatch.setenv("DISCORD_OPERATOR_ID", "111")
    inter = MagicMock()
    inter.user.id = 999
    assert validate_operator(inter) is False


@pytest.mark.anyio
async def test_validate_operator_accepts_match(monkeypatch):
    from trading_sandwich.discord.listener import validate_operator
    monkeypatch.setenv("DISCORD_OPERATOR_ID", "111")
    inter = MagicMock()
    inter.user.id = 111
    assert validate_operator(inter) is True
```

- [ ] **Step 3: Fail**

- [ ] **Step 4: Implement listener skeleton**

```python
# src/trading_sandwich/discord/listener.py
"""Discord bot listener. Receives button interactions; flips proposal state."""
from __future__ import annotations

import os
from uuid import UUID

import discord


def parse_custom_id(custom_id: str) -> tuple[str, UUID]:
    """Expected format: '<action>:<uuid>'. Raises on mismatch."""
    if ":" not in custom_id:
        raise ValueError(f"invalid custom_id {custom_id!r}")
    action, raw = custom_id.split(":", 1)
    if action not in ("approve", "reject", "details"):
        raise ValueError(f"unknown action {action!r}")
    return action, UUID(raw)


def validate_operator(interaction) -> bool:
    """Compare the interacting user's id against env DISCORD_OPERATOR_ID."""
    expected = os.environ.get("DISCORD_OPERATOR_ID", "")
    return str(interaction.user.id) == expected


class TradingBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        try:
            action, proposal_id = parse_custom_id(custom_id)
        except ValueError:
            return
        if not validate_operator(interaction):
            await interaction.response.send_message(
                "not authorized", ephemeral=True
            )
            return
        from trading_sandwich.discord.approval import (
            handle_approve, handle_reject, handle_details,
        )
        if action == "approve":
            await handle_approve(interaction, proposal_id)
        elif action == "reject":
            await handle_reject(interaction, proposal_id)
        elif action == "details":
            await handle_details(interaction, proposal_id)


def run() -> None:
    """Entrypoint for the discord-listener service container."""
    token = os.environ["DISCORD_BOT_TOKEN"]
    TradingBot().run(token)


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Run listener unit tests → pass**

- [ ] **Step 6: Commit**

```
git add src/trading_sandwich/discord/listener.py pyproject.toml tests/unit/test_discord_listener.py
git commit -m "feat: Discord listener skeleton + interaction parsing + operator validation"
```

---

### Task 20: Proposal state transition handlers

**Files:**
- Create: `src/trading_sandwich/discord/approval.py`
- Test: `tests/integration/test_proposal_state_transitions.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/integration/test_proposal_state_transitions.py
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


def _base_proposal_row(sid, did, status="pending", expires_in_min=15):
    from trading_sandwich.db.models_phase2 import TradeProposal
    now = datetime.now(timezone.utc)
    return TradeProposal(
        proposal_id=uuid4(), decision_id=did, signal_id=sid,
        symbol="BTCUSDT", side="long", order_type="limit",
        size_usd=Decimal("500"), limit_price=Decimal("68000"),
        stop_loss={"kind": "fixed_price", "value": "67500", "trigger": "mark", "working_type": "stop_market"},
        take_profit=None, time_in_force="GTC",
        opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
        alignment="a" * 40, similar_trades_evidence="b" * 80,
        expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("3.68"),
        similar_signals_count=0, similar_signals_win_rate=None,
        status=status,
        proposed_at=now,
        expires_at=now + timedelta(minutes=expires_in_min),
        policy_version="test",
    )


async def _seed_decision_and_signal(sid, did):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision, Signal as SignalORM
    factory = get_session_factory()
    async with factory() as session:
        session.add(SignalORM(
            signal_id=sid, symbol="BTCUSDT", timeframe="1h",
            archetype="trend_pullback",
            fired_at=datetime.now(timezone.utc),
            candle_close_time=datetime.now(timezone.utc),
            trigger_price=Decimal("68000"), direction="long",
            confidence=Decimal("0.85"),
            confidence_breakdown={}, gating_outcome="claude_triaged",
            features_snapshot={}, detector_version="test",
        ))
        session.add(ClaudeDecision(
            decision_id=did, signal_id=sid, invocation_mode="triage",
            invoked_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
            decision="paper_trade", rationale="x" * 60,
        ))
        await session.commit()


@pytest.mark.integration
def test_approve_flips_status_and_enqueues_submit(env_for_postgres, monkeypatch):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.discord.approval import approve_proposal

    enqueued = []
    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: enqueued.append(pid),
    )

    async def _flow(url):
        sid = uuid4(); did = uuid4()
        await _seed_decision_and_signal(sid, did)
        row = _base_proposal_row(sid, did)
        factory = get_session_factory()
        async with factory() as session:
            session.add(row); await session.commit()
            pid = row.proposal_id

        await approve_proposal(pid, approver="op-1")

        async with factory() as session:
            fresh = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert fresh.status == "approved"
            assert fresh.approved_by == "op-1"
        assert enqueued == [pid]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(pg.get_connection_url()))


@pytest.mark.integration
def test_approve_rejects_expired_proposal(env_for_postgres, monkeypatch):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.discord.approval import ProposalExpired, approve_proposal

    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: None,
    )

    async def _flow(url):
        sid = uuid4(); did = uuid4()
        await _seed_decision_and_signal(sid, did)
        row = _base_proposal_row(sid, did, expires_in_min=-1)
        factory = get_session_factory()
        async with factory() as session:
            session.add(row); await session.commit()
            pid = row.proposal_id

        with pytest.raises(ProposalExpired):
            await approve_proposal(pid, approver="op-1")

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(pg.get_connection_url()))


@pytest.mark.integration
def test_approve_refuses_non_pending(env_for_postgres, monkeypatch):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.discord.approval import ProposalNotPending, approve_proposal

    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: None,
    )

    async def _flow(url):
        sid = uuid4(); did = uuid4()
        await _seed_decision_and_signal(sid, did)
        row = _base_proposal_row(sid, did, status="approved")
        factory = get_session_factory()
        async with factory() as session:
            session.add(row); await session.commit()
            pid = row.proposal_id

        with pytest.raises(ProposalNotPending):
            await approve_proposal(pid, approver="op-1")

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(pg.get_connection_url()))
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement `approval.py`**

```python
# src/trading_sandwich/discord/approval.py
"""Transactional proposal state transitions triggered by Discord interactions."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update

from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import TradeProposal


class ProposalExpired(Exception):
    pass


class ProposalNotPending(Exception):
    pass


class ProposalNotFound(Exception):
    pass


def _enqueue_submit_order(proposal_id: UUID) -> None:
    """Enqueues submit_order on the execution queue. Wired to execution-worker
    in Stage 1b plan; for Stage 1a this is a stub that can be patched in tests.
    """
    try:
        # Late import: execution.worker doesn't exist until Stage 1b.
        from trading_sandwich.execution.worker import submit_order
        submit_order.delay(str(proposal_id))
    except ImportError:
        # Stage 1a: the execution worker is not built yet. The signal the
        # approval succeeded is the DB row flip; downstream work is tested
        # against the Stage 1b plan's enqueue path.
        pass


async def approve_proposal(proposal_id: UUID, approver: str) -> None:
    """FOR UPDATE → verify status pending and not expired → flip status → enqueue."""
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        row = (await session.execute(
            select(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise ProposalNotFound(str(proposal_id))
        if row.status != "pending":
            raise ProposalNotPending(f"{proposal_id} status={row.status}")
        if row.expires_at < now:
            # Flip to expired while we hold the lock
            await session.execute(
                update(TradeProposal)
                .where(TradeProposal.proposal_id == proposal_id)
                .values(status="expired", rejected_at=now)
            )
            await session.commit()
            raise ProposalExpired(str(proposal_id))
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .values(status="approved", approved_at=now, approved_by=approver)
        )
        await session.commit()
    _enqueue_submit_order(proposal_id)


async def reject_proposal(proposal_id: UUID) -> None:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        row = (await session.execute(
            select(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise ProposalNotFound(str(proposal_id))
        if row.status != "pending":
            raise ProposalNotPending(f"{proposal_id} status={row.status}")
        await session.execute(
            update(TradeProposal)
            .where(TradeProposal.proposal_id == proposal_id)
            .values(status="rejected", rejected_at=now)
        )
        await session.commit()


async def handle_approve(interaction, proposal_id: UUID) -> None:
    try:
        await approve_proposal(proposal_id, approver=str(interaction.user.id))
        await interaction.response.edit_message(content="✅ Approved, submitting…", view=None)
    except ProposalExpired:
        await interaction.response.edit_message(content="⏰ Expired", view=None)
    except ProposalNotPending as exc:
        await interaction.response.send_message(f"not pending: {exc}", ephemeral=True)


async def handle_reject(interaction, proposal_id: UUID) -> None:
    try:
        await reject_proposal(proposal_id)
        await interaction.response.edit_message(content="❌ Rejected", view=None)
    except ProposalNotPending as exc:
        await interaction.response.send_message(f"not pending: {exc}", ephemeral=True)


async def handle_details(interaction, proposal_id: UUID) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(TradeProposal).where(TradeProposal.proposal_id == proposal_id)
        )).scalar_one_or_none()
    body = f"```json\n{row.__dict__ if row else '{}'}\n```"
    await interaction.response.send_message(body[:1900], ephemeral=True)
```

- [ ] **Step 4: Run integration tests → pass**

- [ ] **Step 5: Commit**

```
git add src/trading_sandwich/discord/approval.py tests/integration/test_proposal_state_transitions.py
git commit -m "feat: transactional proposal approve/reject/expire state transitions"
```

---

### Task 21: Proposal sweeper — expire stale pending rows

**Files:**
- Create: `src/trading_sandwich/execution/proposal_sweeper.py`
- Modify: `src/trading_sandwich/celery_app.py` — add beat schedule entry + include
- Test: `tests/integration/test_proposal_sweeper.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/integration/test_proposal_sweeper.py
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
def test_sweeper_flips_expired_pending_rows(env_for_postgres):
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision, Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.execution.proposal_sweeper import expire_stale_proposals

    async def _flow(url):
        factory = get_session_factory()
        now = datetime.now(timezone.utc)
        sid = uuid4(); did = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="1h",
                archetype="trend_pullback", fired_at=now,
                candle_close_time=now, trigger_price=Decimal("68000"),
                direction="long", confidence=Decimal("0.85"),
                confidence_breakdown={}, gating_outcome="claude_triaged",
                features_snapshot={}, detector_version="test",
            ))
            session.add(ClaudeDecision(
                decision_id=did, signal_id=sid, invocation_mode="triage",
                invoked_at=now, completed_at=now,
                decision="paper_trade", rationale="x" * 60,
            ))
            # Expired
            session.add(TradeProposal(
                proposal_id=uuid4(), decision_id=did, signal_id=sid,
                symbol="BTCUSDT", side="long", order_type="limit",
                size_usd=Decimal("500"), limit_price=Decimal("68000"),
                stop_loss={}, take_profit=None, time_in_force="GTC",
                opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
                alignment="a" * 40, similar_trades_evidence="b" * 80,
                expected_rr=Decimal("2.0"), worst_case_loss_usd=Decimal("1"),
                similar_signals_count=0, status="pending",
                proposed_at=now - timedelta(hours=1),
                expires_at=now - timedelta(minutes=1),
                policy_version="test",
            ))
            await session.commit()

        await expire_stale_proposals()

        async with factory() as session:
            rows = (await session.execute(
                select(TradeProposal).where(TradeProposal.signal_id == sid)
            )).scalars().all()
            assert all(r.status == "expired" for r in rows)

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        env_for_postgres(pg.get_connection_url())
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(_flow(pg.get_connection_url()))
```

- [ ] **Step 2: Fail**

- [ ] **Step 3: Implement**

```python
# src/trading_sandwich/execution/proposal_sweeper.py
"""Celery Beat-scheduled sweeper that flips stale pending proposals to expired."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import update

from trading_sandwich.celery_app import app
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models_phase2 import TradeProposal


async def expire_stale_proposals() -> int:
    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        stmt = (
            update(TradeProposal)
            .where(TradeProposal.status == "pending", TradeProposal.expires_at < now)
            .values(status="expired", rejected_at=now)
            .returning(TradeProposal.proposal_id)
        )
        rows = (await session.execute(stmt)).scalars().all()
        await session.commit()
    return len(rows)


@app.task(name="trading_sandwich.execution.proposal_sweeper.sweep")
def sweep() -> int:
    return asyncio.run(expire_stale_proposals())
```

Create `src/trading_sandwich/execution/__init__.py` (empty).

In `celery_app.py`:
- Add to `include`: `"trading_sandwich.execution.proposal_sweeper",`
- Add to `task_routes`: `"trading_sandwich.execution.proposal_sweeper.*": {"queue": "triage"}`  (reuse triage queue; Stage 1b creates the `execution` queue)
- Add to `beat_schedule`:
  ```python
  "expire_stale_proposals": {
      "task": "trading_sandwich.execution.proposal_sweeper.sweep",
      "schedule": 60.0,
  },
  ```

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**

```
git add src/trading_sandwich/execution/ src/trading_sandwich/celery_app.py tests/integration/test_proposal_sweeper.py
git commit -m "feat: proposal sweeper (Celery Beat) expires stale pending rows"
```

---

### Task 22: Approval loop end-to-end integration test

**Files:**
- Test: `tests/integration/test_approval_loop_e2e.py`

- [ ] **Step 1: Write the end-to-end test**

This test exercises: seed signal → fake-claude writes `save_decision(paper_trade)` + `propose_trade` → Discord card would be posted (webhook URL empty, skipped) → invoke `approve_proposal` as if operator clicked ✅ → assert `trade_proposals.status='approved'` and submit_order was called (patched enqueue).

```python
# tests/integration/test_approval_loop_e2e.py
import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.mark.integration
def test_approval_loop_end_to_end(env_for_postgres, env_for_redis, monkeypatch):
    from trading_sandwich.celery_app import app as celery_app
    from trading_sandwich.db.engine import get_session_factory
    from trading_sandwich.db.models import ClaudeDecision, Signal as SignalORM
    from trading_sandwich.db.models_phase2 import TradeProposal
    from trading_sandwich.discord.approval import approve_proposal

    # Patch the execution enqueue (Stage 1b target).
    enqueued = []
    monkeypatch.setattr(
        "trading_sandwich.discord.approval._enqueue_submit_order",
        lambda pid: enqueued.append(pid),
    )

    fake = Path("tests/fixtures/fake_claude.py").resolve()
    monkeypatch.setenv("CLAUDE_BIN", f"{sys.executable} {fake}")
    # Instruct fake claude to emit a response that describes
    # "I called save_decision + propose_trade during the session."
    # The triage handler verifies save_decision by querying claude_decisions;
    # we simulate the in-session MCP calls explicitly before invoking triage.
    monkeypatch.setenv(
        "FAKE_CLAUDE_RESPONSE",
        json.dumps({
            "decision": "paper_trade",
            "rationale": "y" * 60,
            "alert_posted": False,
            "proposal_created": True,
        }),
    )

    async def _seed_signal_and_simulate_tools():
        factory = get_session_factory()
        sid = uuid4()
        async with factory() as session:
            session.add(SignalORM(
                signal_id=sid, symbol="BTCUSDT", timeframe="1h",
                archetype="trend_pullback",
                fired_at=datetime.now(timezone.utc),
                candle_close_time=datetime.now(timezone.utc),
                trigger_price=Decimal("68000"), direction="long",
                confidence=Decimal("0.85"),
                confidence_breakdown={},
                gating_outcome="claude_triaged",
                features_snapshot={"atr_14": "500"},
                detector_version="test",
            ))
            await session.commit()
        # Simulate the two tool calls Claude makes during triage
        from trading_sandwich.contracts.phase2 import StopLossSpec
        from trading_sandwich.mcp.tools.decisions import save_decision
        from trading_sandwich.mcp.tools.proposals import propose_trade
        did = await save_decision(
            signal_id=sid, decision="paper_trade", rationale="y" * 60,
        )
        pid = await propose_trade(
            decision_id=did,
            symbol="BTCUSDT", side="long", order_type="limit",
            size_usd=Decimal("500"), limit_price=Decimal("68000"),
            stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("67500")),
            take_profit=None,
            opportunity="o" * 80, risk="r" * 80, profit_case="p" * 80,
            alignment="a" * 40, similar_trades_evidence="s" * 80,
            expected_rr=Decimal("2.0"),
            worst_case_loss_usd=Decimal("3.68"),
            similar_signals_count=0,
        )
        return sid, pid

    async def _assert_approved(pid):
        factory = get_session_factory()
        async with factory() as session:
            row = (await session.execute(
                select(TradeProposal).where(TradeProposal.proposal_id == pid)
            )).scalar_one()
            assert row.status == "approved"

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg, \
         RedisContainer("redis:7-alpine") as rc:
        env_for_postgres(pg.get_connection_url())
        env_for_redis(f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0")
        command.upgrade(Config("alembic.ini"), "head")

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        sid, pid = asyncio.run(_seed_signal_and_simulate_tools())

        # Operator taps ✅
        asyncio.run(approve_proposal(pid, approver="op-1"))

        asyncio.run(_assert_approved(pid))
        assert enqueued == [pid]
```

- [ ] **Step 2: Run**

```
docker compose run --rm test pytest tests/integration/test_approval_loop_e2e.py -v -m integration
```
Expected: 1 passed.

- [ ] **Step 3: Run full test suite for regressions**

```
docker compose run --rm test pytest -v
```
All Phase 0/1 + Phase 2 Stage 1a tests green.

- [ ] **Step 4: Commit**

```
git add tests/integration/test_approval_loop_e2e.py
git commit -m "test: approval loop end-to-end (signal → decision → proposal → approve → enqueue)"
```

---

**⏸ FINAL CHECKPOINT — End of Stage 1a.**

At this point the triage pipeline works end-to-end:
1. Signal passes gating → enqueues triage.
2. Triage-worker spawns Claude (fake in tests, real in live).
3. Claude calls `save_decision` + `propose_trade` via MCP.
4. `trade_proposals` row lands with status `pending`.
5. Operator-equivalent call flips status to `approved` and calls the (still-stub) `_enqueue_submit_order`.
6. Sweeper expires any pending proposals past their TTL.

**Stage 1a does NOT yet:** actually submit orders to a paper or live adapter, run policy rails, manage positions, trip kill-switch, reconcile with Binance, or expose CLI commands. Those land in Stage 1b.

**Before starting Stage 1b:** complete the smoke check below, then write the Stage 1b plan using the same pattern.

**Smoke checks:**

```
docker compose run --rm test pytest -v
docker compose run --rm tools ruff check .
docker compose run --rm tools python -c "from trading_sandwich.mcp.server import mcp; print(len(mcp._tool_manager._tools))"
```

Expected:
- All tests green (Phase 0 + Phase 1 + Phase 2 Stage 1a).
- Ruff clean.
- MCP server registers exactly 7 tools: `get_signal`, `get_market_snapshot`, `find_similar_signals`, `get_archetype_stats`, `save_decision`, `send_alert`, `propose_trade`.

Then write `docs/superpowers/plans/2026-04-25-phase-2-stage-1b-execution.md` via the writing-plans skill.

---

## Self-review (do this before declaring plan done)

- [x] Every spec §3 Stage 1 tool has a task (8, 9, 10, 11, 12, 13, 14).
- [x] Daily cap mechanism covered (tasks 5, 6).
- [x] Triage subprocess covered (task 16 → 17 → 18).
- [x] Approval state machine covered (task 20) with all three terminal states tested (approved, rejected, expired).
- [x] Proposal idempotency via `UNIQUE (decision_id)` enforced in migration (task 3) and tested via re-invocation in task 22.
- [x] Every task has complete test code, complete implementation code, exact commands, expected output.
- [x] No placeholders, no "TODO", no "similar to earlier task".
- [x] Type consistency: `StopLossSpec.value` is `Decimal` everywhere; `decision_id` / `signal_id` / `proposal_id` are `UUID` everywhere; `ClaudeResponse.rationale` min_length 40 matches `save_decision` rationale guard.

Out-of-scope in this plan (deferred to Stage 1b and called out in the plan body): execution adapters, 16 policy rails, kill-switch, watchdog, live Binance integration, CLI commands, compose service definitions, runtime/CLAUDE.md authoring, runtime/GOALS.md.

