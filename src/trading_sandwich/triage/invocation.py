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

    The agent runs with cwd=<workspace>/runtime so it auto-loads the
    *trader* CLAUDE.md (the policy that says "you are a veteran trader,
    here is your invocation contract"), not the *dev* CLAUDE.md at the
    project root (which says "this is not the runtime brain"). The MCP
    config is passed via --mcp-config so the three MCP servers
    (trading + tradingview + binance) resolve regardless of cwd.

    Raises:
        InvocationTimeout: if CLAUDE_TIMEOUT_S is exceeded.
        InvocationError: if claude exits non-zero.
        ValueError: if stdout cannot be parsed.
    """
    timeout_s = float(os.environ.get("CLAUDE_TIMEOUT_S", "90"))
    prompt_version = _git_sha(workspace)
    env = {**os.environ, "TS_PROMPT_VERSION": prompt_version}

    runtime_cwd = workspace / "runtime"
    mcp_config = workspace / ".mcp.json"

    # cwd=runtime so runtime/CLAUDE.md auto-loads via Claude Code's
    # CLAUDE.md discovery — that's the trader brain.
    # --mcp-config: explicit MCP server bundle.
    # --strict-mcp-config: ignore user-level / claude.ai-account leak-ins.
    # --append-system-prompt-file: belt-and-suspenders — ensure trader
    # CLAUDE.md is in context even if discovery walks up the tree.
    # --allowedTools: pre-authorize exactly the MCP tools the triage
    # agent uses, including read-only verification calls. This avoids
    # the per-call permission prompt (which blocks non-interactive runs)
    # without using --dangerously-skip-permissions (root-blocked).
    # Note: forbidden Binance order tools (per hard rule §5) are NOT
    # allowlisted, so even if the agent tried, the call would be denied.
    allowed_tools = ",".join([
        # Our system MCP — all 7 tools.
        "mcp__tsandwich__get_signal",
        "mcp__tsandwich__get_market_snapshot",
        "mcp__tsandwich__find_similar_signals",
        "mcp__tsandwich__get_archetype_stats",
        "mcp__tsandwich__save_decision",
        "mcp__tsandwich__send_alert",
        "mcp__tsandwich__propose_trade",
        # Verification layer — TradingView (read-only). Wildcard the
        # whole server since the agent may pick any.
        "mcp__tradingview",
        # Verification layer — Binance read-only.
        "mcp__binance__binanceAccountInfo",
        "mcp__binance__binanceAccountSnapshot",
        "mcp__binance__binanceOrderBook",
    ])

    # Model + effort: Sonnet at low effort is the right choice for triage.
    # We're making structured tool-call → JSON decisions, not multi-step
    # research. Opus at default effort burns 5-10× the Max session quota
    # for marginal-at-best decision quality on this task.
    # Override either via env var if a more cautious model is needed for
    # specific archetypes (Phase 3+).
    model = os.environ.get("TRIAGE_CLAUDE_MODEL", "sonnet")
    effort = os.environ.get("TRIAGE_CLAUDE_EFFORT", "low")

    cmd = _resolve_claude_cmd() + [
        "--model", model,
        "--effort", effort,
        "--strict-mcp-config",
        "--mcp-config", str(mcp_config),
        "--append-system-prompt-file", str(runtime_cwd / "CLAUDE.md"),
        "--allowedTools", allowed_tools,
        "-p", f"{mode} {signal_id}",
    ]

    try:
        result = subprocess.run(
            cmd, cwd=str(runtime_cwd), env=env,
            capture_output=True, text=True, timeout=timeout_s,
            stdin=subprocess.DEVNULL,
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
