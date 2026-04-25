from pathlib import Path

import pytest

from trading_sandwich.mcp.tools.state_diary import (
    append_diary,
    read_diary,
    write_state,
)


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
