from __future__ import annotations

from datetime import datetime
from enum import Enum

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
