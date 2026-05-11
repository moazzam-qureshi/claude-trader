"""Phase 3 Wave 1 Task 2.28 — runtime/SOUL.md + GOALS.md
portfolio-strategist content checks.

Pins the required markers of the rewritten identity (SOUL.md) and
mandate (GOALS.md) files, plus that the heartbeat-trader originals
are preserved as .heartbeat-trader.bak. Markers, not exact prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
_SOUL = _RUNTIME / "SOUL.md"
_GOALS = _RUNTIME / "GOALS.md"
_SOUL_BAK = _RUNTIME / "SOUL.md.heartbeat-trader.bak"
_GOALS_BAK = _RUNTIME / "GOALS.md.heartbeat-trader.bak"


@pytest.fixture
def soul() -> str:
    return _SOUL.read_text(encoding="utf-8")


@pytest.fixture
def goals() -> str:
    return _GOALS.read_text(encoding="utf-8")


# ---------- SOUL.md: portfolio-strategist identity ----------


def test_soul_identity_is_portfolio_strategist(soul):
    lowered = soul.lower().replace("**", "")
    assert "portfolio strategist" in lowered
    # The hard rule: does not place trades directly.
    assert (
        "i do not make individual trades" in lowered
        or "i don't make individual trades" in lowered
        or "i do not place" in lowered
        or "i never place an order" in lowered
    )
    # Strategies are what trade.
    assert "strategies make" in lowered or "strategies trade" in lowered


def test_soul_not_old_discretionary_trader_identity(soul):
    lowered = soul.lower()
    # The old SOUL opened "I am a discretionary crypto trader running a
    # small, owner-operated book on Binance spot margin (3x max)."
    assert "spot margin" not in lowered
    assert "3x max" not in lowered
    assert "discretionary crypto trader" not in lowered


def test_soul_keeps_halal_spot(soul):
    lowered = soul.lower()
    assert "halal" in lowered
    assert "longs only" in lowered or "long-only" in lowered or "longs-only" in lowered


def test_soul_mentions_allocation_thinking(soul):
    lowered = soul.lower()
    # The new identity thinks in allocations / which strategies run.
    assert "allocat" in lowered
    assert "regime" in lowered


# ---------- GOALS.md: allocate-strategies mandate ----------


def test_goals_mandate_is_allocation(goals):
    lowered = goals.lower().replace("**", "")
    # The mandate: allocate mechanical strategies, not propose trades.
    assert "allocat" in lowered
    assert "strateg" in lowered
    # Should NOT still describe propose_trade as the primary action.
    assert "propose_trade" not in lowered or "frozen" in lowered or "no longer" in lowered


def test_goals_no_old_position_sizing_table(goals):
    lowered = goals.lower()
    # The old GOALS had a "Setup quality | win_rate | RR | sample |
    # size" table tied to individual proposals. The strategist doesn't
    # size individual trades.
    assert "textbook trend_pullback" not in lowered
    assert "marginal liquidity_sweep" not in lowered


def test_goals_keeps_halal_spot(goals):
    lowered = goals.lower()
    assert "halal" in lowered
    assert "long" in lowered  # longs only / long-only


def test_goals_mentions_decision_classes_or_strategy_lifecycle(goals):
    lowered = goals.lower()
    # GOALS should reference the strategist's actual levers.
    assert (
        "deploy" in lowered
        or "wind_down" in lowered
        or "wind down" in lowered
        or "pause" in lowered
    )


def test_goals_drawdown_ceiling_present(goals):
    # The book-level risk ceiling should still be stated.
    lowered = goals.lower()
    assert "drawdown" in lowered


# ---------- Backups preserved ----------


def test_soul_backup_exists():
    assert _SOUL_BAK.exists()


def test_goals_backup_exists():
    assert _GOALS_BAK.exists()


def test_soul_backup_is_old_persona():
    bak = _SOUL_BAK.read_text(encoding="utf-8")
    assert "discretionary crypto trader" in bak


def test_goals_backup_is_old_persona():
    bak = _GOALS_BAK.read_text(encoding="utf-8")
    assert "Compound the book" in bak
