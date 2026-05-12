"""The heartbeat loop now invokes the *portfolio-strategist* persona, not
the (frozen) discretionary trader. Two contracts pinned here:

1. `triage.heartbeat.ALLOWED_TOOLS` exposes the strategist's MCP surface
   (strategy lifecycle + allocation + regime + diary/state + alert) and
   NOT the dead trader tools (`propose_trade` is frozen behind
   `emergency_override=True`; signal-triage tools belong to a path
   nothing drives anymore).

2. `policy.yaml`'s `heartbeat.interval_minutes` pins the cadence to 6
   hours (min == max == 360 — the operator wants a fixed 6-hourly
   strategist review, in line with the persona's 6-24h design, not Claude
   self-pacing) and the daily/weekly shift caps allow it (≥ 4/day,
   ≥ 28/week, with headroom).
"""
from __future__ import annotations

from pathlib import Path

import yaml


def test_allowed_tools_is_the_strategist_surface():
    from trading_sandwich.triage.heartbeat import ALLOWED_TOOLS

    must_have = {
        # strategy lifecycle
        "mcp__tsandwich__list_strategies",
        "mcp__tsandwich__get_strategy_performance",
        "mcp__tsandwich__get_account_allocation",
        "mcp__tsandwich__get_regime_signals",
        "mcp__tsandwich__deploy_strategy",
        "mcp__tsandwich__pause_strategy",
        "mcp__tsandwich__resume_strategy",
        "mcp__tsandwich__wind_down_strategy",
        "mcp__tsandwich__adjust_allocation",
        "mcp__tsandwich__adjust_params",
        "mcp__tsandwich__override_regime",
        # account / market context
        "mcp__tsandwich__get_open_positions",
        "mcp__tsandwich__get_pipeline_health",
        # diary / state / comms
        "mcp__tsandwich__read_diary",
        "mcp__tsandwich__write_state",
        "mcp__tsandwich__append_diary",
        "mcp__tsandwich__send_alert",
        "mcp__tsandwich__notify_operator",
    }
    missing = must_have - set(ALLOWED_TOOLS)
    assert not missing, f"strategist tools missing from ALLOWED_TOOLS: {missing}"

    must_not_have = {
        "mcp__tsandwich__propose_trade",   # frozen behind emergency_override
        "mcp__tsandwich__get_signal",       # signal-triage path, nothing drives it
        "mcp__tsandwich__find_similar_signals",
        "mcp__tsandwich__get_archetype_stats",
        "mcp__tsandwich__save_decision",
        "mcp__tsandwich__get_recent_signals",
    }
    leaked = must_not_have & set(ALLOWED_TOOLS)
    assert not leaked, f"trader-only tools leaked into ALLOWED_TOOLS: {leaked}"


def test_heartbeat_cadence_is_pinned_to_6_hours():
    raw = yaml.safe_load(Path("policy.yaml").read_text())
    hb = raw["heartbeat"]
    assert hb["interval_minutes"]["min"] == 360
    assert hb["interval_minutes"]["max"] == 360
    # a fixed 6-hourly review = 4/day, 28/week — caps must allow it
    assert hb["daily_shift_cap"] >= 4
    assert hb["weekly_shift_cap"] >= 28
