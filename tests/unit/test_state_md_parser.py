from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_sandwich.contracts.heartbeat import StateFrontmatter
from trading_sandwich.triage.state_io import (
    BODY_MAX_CHARS,
    StateIOError,
    read_state,
    write_state,
)


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
