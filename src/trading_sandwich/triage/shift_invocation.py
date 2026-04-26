"""Shift invocation — build argv to spawn `claude` for a heartbeat shift,
and run the subprocess with timeout."""
from __future__ import annotations

import asyncio
import os
import time
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
    """Construct the argv list for spawning Claude for a heartbeat shift.

    Five files are passed via --append-system-prompt-file:
      CLAUDE.md, SOUL.md, GOALS.md, STATE.md, today's diary.
    cwd should be runtime_dir so any in-tree CLAUDE.md auto-discovery still
    works (belt-and-suspenders with the explicit append).
    """
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
    ]
    # Repeated --allowedTools flags (not comma-separated). The Claude CLI
    # accepts both shapes inconsistently across versions; repeated flag is
    # the version that always works.
    for tool in allowed_tools:
        argv.extend(["--allowedTools", tool])
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
        return ShiftRunResult(
            returncode=-1, stdout="", stderr="timeout",
            duration_seconds=timeout_seconds,
        )
    duration = int(time.monotonic() - start)
    return ShiftRunResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        duration_seconds=duration,
    )
