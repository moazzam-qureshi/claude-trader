from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trading_sandwich.contracts.heartbeat import (
    ShiftRecord,
    StateFrontmatter,
    UniverseEventType,
    UniverseMutationRequest,
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
            next_check_in_minutes=10,
            next_check_reason="too soon",
        )
    with pytest.raises(ValidationError):
        StateFrontmatter(
            shift_count=0,
            last_updated=datetime.now(timezone.utc),
            open_positions=0,
            open_theses=0,
            regime="bootstrap",
            next_check_in_minutes=300,
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
            rationale="x" * 20,
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
