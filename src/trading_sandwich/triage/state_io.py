"""STATE.md read/write and diary append/rotate. Pure I/O — no DB, no MCP."""
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
