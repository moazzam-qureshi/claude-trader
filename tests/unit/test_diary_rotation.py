from datetime import date
from pathlib import Path

from trading_sandwich.triage.state_io import (
    append_diary,
    diary_path_for,
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
