from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_save_decision_rejects_live_order():
    from trading_sandwich.mcp.tools.decisions import save_decision

    with pytest.raises(ValueError, match="live_order"):
        await save_decision(
            signal_id=uuid4(),
            decision="live_order",  # type: ignore[arg-type]
            rationale="x" * 60,
        )


@pytest.mark.anyio
async def test_save_decision_requires_rationale_min_length():
    from trading_sandwich.mcp.tools.decisions import save_decision

    with pytest.raises(ValueError, match="rationale"):
        await save_decision(
            signal_id=uuid4(),
            decision="alert",
            rationale="too short",
        )


@pytest.mark.anyio
async def test_save_decision_alert_requires_payload():
    from trading_sandwich.mcp.tools.decisions import save_decision

    with pytest.raises(ValueError, match="alert_payload"):
        await save_decision(
            signal_id=uuid4(),
            decision="alert",
            rationale="x" * 60,
            alert_payload=None,
        )
