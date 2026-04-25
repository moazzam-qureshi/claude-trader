from uuid import uuid4

import pytest

from trading_sandwich.contracts.phase2 import AlertPayload


@pytest.mark.anyio
async def test_send_alert_rejects_unknown_channel():
    from trading_sandwich.mcp.tools.alerts import send_alert

    with pytest.raises(ValueError, match="channel"):
        await send_alert(
            channel="slack",  # type: ignore[arg-type]
            payload=AlertPayload(title="t", body="b", signal_id=uuid4(), decision_id=uuid4()),
        )
