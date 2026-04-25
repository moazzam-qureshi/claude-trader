from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_trip_writes_active_true():
    from trading_sandwich.execution.kill_switch import trip
    with patch("trading_sandwich.execution.kill_switch._update_state",
               AsyncMock()) as upd:
        await trip(reason="max_daily_realized_loss_breached")
    upd.assert_awaited_once()
    args, kwargs = upd.await_args
    assert args[0] is True
    assert "max_daily" in args[1]


@pytest.mark.anyio
async def test_resume_requires_ack_reason():
    from trading_sandwich.execution.kill_switch import resume
    with pytest.raises(ValueError, match="ack_reason"):
        await resume(ack_reason="")
