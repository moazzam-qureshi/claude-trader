"""Phase 3 Wave 1 Task 2.29 — propose_trade is frozen unless
emergency_override=True.

The Phase 2.7 discretionary-trader path is closed: calling
propose_trade without emergency_override=True raises immediately,
before any sizing / cross-checks / DB write. The signal-worker keeps
running for analytics (the signals dataset still grows), but no
trading loop consumes it. With emergency_override=True the tool runs
its normal cross-checks (so it can still be used for a hand-pulled
emergency action, e.g. flatten a position the strategist needs gone).

These tests pin only the freeze gate — the existing propose_trade
cross-check behaviour is covered by the existing
test_mcp_tool_propose_trade* suites.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from trading_sandwich.mcp.tools.proposals import propose_trade


def _common_kwargs() -> dict:
    """A minimal kwargs set for propose_trade — enough to get past the
    signature, since the freeze gate must reject *before* any of these
    are validated."""
    return dict(
        decision_id=uuid4(),
        symbol="BTCUSDT",
        side="long",
        order_type="market",
        limit_price=None,
        stop_loss=None,
        take_profit=None,
        opportunity="x",
        risk="x",
        profit_case="x",
        alignment="x",
        similar_trades_evidence="x",
        expected_rr=Decimal("2.0"),
        worst_case_loss_usd=Decimal("5"),
        similar_signals_count=10,
    )


def test_propose_trade_has_emergency_override_param():
    sig = inspect.signature(propose_trade)
    assert "emergency_override" in sig.parameters
    # It must default to False — the frozen state is the default.
    assert sig.parameters["emergency_override"].default is False


@pytest.mark.anyio
async def test_propose_trade_rejected_without_override():
    with pytest.raises(ValueError, match="frozen|emergency_override|discretionary"):
        await propose_trade(**_common_kwargs())


@pytest.mark.anyio
async def test_propose_trade_rejected_with_override_false_explicit():
    with pytest.raises(ValueError, match="frozen|emergency_override|discretionary"):
        await propose_trade(emergency_override=False, **_common_kwargs())


@pytest.mark.anyio
async def test_freeze_gate_rejects_before_other_validation():
    """The freeze gate fires before RR / sizing / DB checks — so a
    proposal with an obviously-bad expected_rr still gets the *freeze*
    error, not an RR error, when override is not set."""
    kwargs = _common_kwargs()
    kwargs["expected_rr"] = Decimal("0.01")  # would fail rr_min later
    with pytest.raises(ValueError) as exc:
        await propose_trade(**kwargs)
    msg = str(exc.value).lower()
    assert "frozen" in msg or "emergency_override" in msg or "discretionary" in msg
    # ...and NOT the rr_min message
    assert "default_rr_minimum" not in msg
