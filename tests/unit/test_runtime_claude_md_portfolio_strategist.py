"""Phase 3 Wave 1 Task 2.27 — runtime/CLAUDE.md portfolio-strategist
content checks.

Pins the required *markers* of the rewritten runtime brain (per spec
§3.7), not its exact prose. The shift to "portfolio strategist" is
structural — Claude commands strategies, never places trades — so the
file must say so unambiguously, list the nine decision classes, point
at the strategy MCP tools, and keep the HALAL-spot warning intact.

Also checks the heartbeat-trader version is preserved at
runtime/CLAUDE.md.heartbeat-trader.bak so the old persona isn't lost.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
_CLAUDE_MD = _RUNTIME / "CLAUDE.md"
_BAK = _RUNTIME / "CLAUDE.md.heartbeat-trader.bak"


@pytest.fixture
def text() -> str:
    return _CLAUDE_MD.read_text(encoding="utf-8")


# ---------- HALAL warning preserved ----------


def test_halal_spot_warning_present(text):
    assert "HALAL" in text
    # The longs-only and no-leverage rules must survive the rewrite.
    lowered = text.lower()
    assert "longs only" in lowered or "long-only" in lowered or "longs-only" in lowered
    assert "no leverage" in lowered or "max_leverage" in lowered


# ---------- Portfolio-strategist identity ----------


def test_identity_is_portfolio_strategist(text):
    # Markdown emphasis (**not**) is noise for this check — strip it.
    lowered = text.lower().replace("**", "")
    assert "portfolio strategist" in lowered
    # The hard architectural rule: Claude does not place trades.
    assert (
        "do not make individual trades" in lowered
        or "cannot place orders directly" in lowered
        or "does not make individual trades" in lowered
        or "you never place an order" in lowered
    )
    # Strategies are what trade.
    assert (
        "strategies make trades" in lowered
        or "strategies make the trades" in lowered
        or "strategies trade mechanically" in lowered
        or "strategies make trades —" in lowered
    )


def test_no_heartbeat_trader_identity_language(text):
    # The new file should not describe itself as the heartbeat *trader*
    # protocol — that's the old persona. (The word "heartbeat" may
    # still appear when referencing the old backup or the cadence
    # mechanism, so we only forbid the specific old title.)
    assert "Heartbeat Shift Protocol" not in text


# ---------- Nine decision classes ----------


@pytest.mark.parametrize("decision_class", [
    "SUPERVISE", "ALERT", "ADJUST", "PAUSE", "DEPLOY",
    "WIND_DOWN", "REGIME_OVERRIDE", "CURATE", "OBSERVE",
])
def test_decision_class_documented(text, decision_class):
    assert decision_class in text, f"decision class {decision_class} missing"


# ---------- Strategy MCP tool reference ----------


@pytest.mark.parametrize("tool_name", [
    "deploy_strategy", "pause_strategy", "resume_strategy",
    "wind_down_strategy", "adjust_allocation", "adjust_params",
    "override_regime", "list_strategies", "get_strategy_performance",
    "get_account_allocation", "get_regime_signals",
])
def test_strategy_tool_referenced(text, tool_name):
    assert tool_name in text, f"MCP tool {tool_name} not referenced"


# ---------- Cadence shift ----------


def test_cadence_is_slower(text):
    lowered = text.lower()
    # Spec §3.7: shifts every 6-24 hours, not 15-240 min.
    assert "6" in text and "24" in text
    assert "hour" in lowered


# ---------- Backup preserved ----------


def test_heartbeat_trader_backup_exists():
    assert _BAK.exists(), (
        "runtime/CLAUDE.md.heartbeat-trader.bak must preserve the old "
        "heartbeat-trader persona"
    )


def test_backup_is_the_old_persona():
    bak = _BAK.read_text(encoding="utf-8")
    assert "Heartbeat Shift Protocol" in bak
