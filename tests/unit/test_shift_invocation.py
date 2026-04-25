from pathlib import Path

from trading_sandwich.triage.shift_invocation import build_claude_argv


def test_build_argv_includes_all_prompt_files(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for fname in ("CLAUDE.md", "SOUL.md", "GOALS.md", "STATE.md"):
        (runtime / fname).write_text("x")
    (runtime / "diary").mkdir()
    (runtime / "diary" / "2026-04-26.md").write_text("y")

    argv = build_claude_argv(
        runtime_dir=runtime,
        today_diary=runtime / "diary" / "2026-04-26.md",
        mcp_config_path=Path("/app/.mcp.json"),
        allowed_tools=["mcp__tsandwich__read_diary"],
    )
    assert "claude" in argv[0]
    assert "--model" in argv and "sonnet" in argv
    assert "--strict-mcp-config" in argv
    assert "--mcp-config" in argv
    assert any(str(runtime / "CLAUDE.md") in a for a in argv)
    assert any(str(runtime / "SOUL.md") in a for a in argv)
    assert any(str(runtime / "diary" / "2026-04-26.md") in a for a in argv)
    assert any("mcp__tsandwich__read_diary" in a for a in argv)
