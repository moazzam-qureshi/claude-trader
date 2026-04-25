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
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return shlex.split(override)
    return ["claude"]


def invoke_claude(signal_id: UUID, workspace: Path, mode: str = "triage") -> ClaudeResponse:
    """Spawn `claude -p`. Parse the final JSON line as a ClaudeResponse.

    Raises:
        InvocationTimeout: if CLAUDE_TIMEOUT_S is exceeded.
        InvocationError: if claude exits non-zero.
        ValueError: if stdout cannot be parsed.
    """
    timeout_s = float(os.environ.get("CLAUDE_TIMEOUT_S", "90"))
    prompt_version = _git_sha(workspace)
    env = {**os.environ, "TS_PROMPT_VERSION": prompt_version}

    cmd = _resolve_claude_cmd() + ["-p", f"{mode} {signal_id}"]

    try:
        result = subprocess.run(
            cmd, cwd=str(workspace), env=env,
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise InvocationTimeout(
            f"claude timed out after {timeout_s}s (signal {signal_id})"
        ) from exc

    if result.returncode != 0:
        raise InvocationError(
            f"claude exited {result.returncode}: {result.stderr[:500]}"
        )

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
