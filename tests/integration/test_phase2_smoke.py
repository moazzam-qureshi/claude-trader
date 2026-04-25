import asyncio
import json
from pathlib import Path


def test_compose_has_phase2_services():
    """All four Phase 2 services declared in compose."""
    compose = Path("docker-compose.yml").read_text()
    for svc in ["mcp-server:", "triage-worker:", "discord-listener:", "execution-worker:"]:
        assert svc in compose, f"{svc} missing from docker-compose.yml"


def test_mcp_json_present():
    assert Path(".mcp.json").exists()
    cfg = json.loads(Path(".mcp.json").read_text())
    assert "trading" in cfg["mcpServers"]


def test_runtime_files_present():
    assert Path("runtime/CLAUDE.md").exists()
    assert Path("runtime/GOALS.md").exists()
    claude_md = Path("runtime/CLAUDE.md").read_text(encoding="utf-8")
    assert len(claude_md) > 5000
    assert "veteran" in claude_md.lower()


def test_dockerfile_has_triage_worker_stage():
    df = Path("Dockerfile").read_text()
    assert "FROM base AS triage-worker" in df
    assert "@anthropic-ai/claude-code" in df


def test_seven_mcp_tools_registered():
    """Server module imports all four tool modules at boot; FastMCP
    list_tools() reports the registered set."""
    from trading_sandwich.mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "get_signal", "get_market_snapshot", "find_similar_signals",
        "get_archetype_stats", "save_decision", "send_alert", "propose_trade",
    }
    assert expected.issubset(names), f"missing: {expected - names}"
