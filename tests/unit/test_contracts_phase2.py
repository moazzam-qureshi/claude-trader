from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trading_sandwich.contracts.phase2 import (
    AlertPayload,
    ClaudeResponse,
    OrderRequest,
    StopLossSpec,
    TakeProfitSpec,
)


def test_stop_loss_spec_requires_value():
    spec = StopLossSpec(kind="fixed_price", value=Decimal("68000"))
    assert spec.kind == "fixed_price"
    assert spec.trigger == "mark"
    assert spec.working_type == "stop_market"


def test_stop_loss_spec_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        StopLossSpec(kind="bogus", value=Decimal("1"))


def test_claude_response_valid_decision():
    resp = ClaudeResponse(decision="alert", rationale="x" * 50)
    assert resp.decision == "alert"


def test_claude_response_rejects_live_order():
    with pytest.raises(ValidationError):
        ClaudeResponse(decision="live_order", rationale="x" * 50)


def test_claude_response_requires_rationale_min_length():
    with pytest.raises(ValidationError):
        ClaudeResponse(decision="alert", rationale="short")


def test_order_request_requires_stop_loss():
    with pytest.raises(ValidationError):
        OrderRequest(
            symbol="BTCUSDT",
            side="long",
            order_type="market",
            size_usd=Decimal("500"),
            stop_loss=None,  # type: ignore[arg-type]
            client_order_id="x",
        )


def test_order_request_direction_defaults_to_buy():
    req = OrderRequest(
        symbol="BTCUSDT", side="long", order_type="market",
        size_usd=Decimal("100"),
        stop_loss=StopLossSpec(kind="structural", value=Decimal("0")),
        client_order_id="x",
    )
    assert req.direction == "buy"
    assert req.side == "long"


def test_order_request_direction_can_be_sell():
    req = OrderRequest(
        symbol="BTCUSDT", side="long", direction="sell", order_type="limit",
        size_usd=Decimal("50"), limit_price=Decimal("70000"),
        stop_loss=StopLossSpec(kind="structural", value=Decimal("0")),
        client_order_id="y",
    )
    assert req.direction == "sell"
    assert req.side == "long"  # halal — a sell only reduces the long


def test_order_request_rejects_invalid_direction():
    with pytest.raises(ValidationError):
        OrderRequest(
            symbol="BTCUSDT", side="long", direction="short",  # type: ignore[arg-type]
            order_type="market", size_usd=Decimal("10"),
            stop_loss=StopLossSpec(kind="structural", value=Decimal("0")),
            client_order_id="z",
        )


def test_alert_payload_structure():
    payload = AlertPayload(
        title="x", body="y", signal_id=uuid4(), decision_id=uuid4()
    )
    assert payload.title == "x"


def test_take_profit_rr_ratio_kind():
    tp = TakeProfitSpec(kind="rr_ratio", value=Decimal("2.0"))
    assert tp.value == Decimal("2.0")
