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
    by_bucket: list[dict]
