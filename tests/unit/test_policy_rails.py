from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from trading_sandwich.contracts.phase2 import AccountState


def _proposal(**overrides):
    """Build a minimal proposal dict matching the TradeProposal ORM shape."""
    base = {
        "proposal_id": uuid4(),
        "symbol": "BTCUSDT", "side": "long", "order_type": "market",
        "size_usd": Decimal("500"), "limit_price": None,
        "stop_loss": {"kind": "fixed_price", "value": "67000"},
        "take_profit": None,
        "expected_rr": Decimal("2.0"),
        "policy_version": "test",
    }
    base.update(overrides)
    from types import SimpleNamespace
    return SimpleNamespace(**base)


def _account(**overrides):
    base = {
        "equity_usd": Decimal("10000"),
        "free_margin_usd": Decimal("8000"),
        "unrealized_pnl_usd": Decimal("0"),
        "realized_pnl_today_usd": Decimal("0"),
        "open_positions_count": 0,
        "leverage_used": Decimal("0"),
    }
    base.update(overrides)
    return AccountState(**base)


@pytest.mark.anyio
async def test_rail_kill_switch_blocks(monkeypatch):
    from trading_sandwich.execution import policy_rails
    monkeypatch.setattr(
        policy_rails, "_kill_switch_active", AsyncMock(return_value=True),
    )
    block = await policy_rails.rail_kill_switch(_proposal(), _account())
    assert block is not None
    assert "kill_switch" in block


@pytest.mark.anyio
async def test_rail_trading_disabled_blocks(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.policy_rails import rail_trading_enabled
    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: False)
    block = await rail_trading_enabled(_proposal(), _account())
    assert block is not None


@pytest.mark.anyio
async def test_rail_max_order_usd_blocks(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.policy_rails import rail_max_order_usd
    monkeypatch.setattr(_policy, "get_max_order_usd", lambda: Decimal("500"))
    block = await rail_max_order_usd(_proposal(size_usd=Decimal("1000")), _account())
    assert block is not None


@pytest.mark.anyio
async def test_rail_stop_loss_required_blocks_when_missing():
    from trading_sandwich.execution.policy_rails import rail_stop_loss_required
    block = await rail_stop_loss_required(_proposal(stop_loss=None), _account())
    assert block is not None


@pytest.mark.anyio
async def test_rail_max_leverage_blocks(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.policy_rails import rail_max_leverage
    monkeypatch.setattr(_policy, "load_policy", lambda: {"max_leverage": 2})
    block = await rail_max_leverage(
        _proposal(),
        _account(leverage_used=Decimal("3")),
    )
    assert block is not None


@pytest.mark.anyio
async def test_rail_universe_allowlist_blocks(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.policy_rails import rail_universe_allowlist
    monkeypatch.setattr(
        _policy, "get_universe_symbols", lambda: ["BTCUSDT", "ETHUSDT"],
    )
    block = await rail_universe_allowlist(_proposal(symbol="DOGEUSDT"), _account())
    assert block is not None


@pytest.mark.anyio
async def test_rail_account_state_sanity_blocks_thin_margin():
    from trading_sandwich.execution.policy_rails import rail_account_state_sanity
    block = await rail_account_state_sanity(
        _proposal(size_usd=Decimal("500")),
        _account(free_margin_usd=Decimal("100")),
    )
    assert block is not None


@pytest.mark.anyio
async def test_rail_max_daily_realized_loss_blocks(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.policy_rails import rail_max_daily_realized_loss
    monkeypatch.setattr(_policy, "load_policy", lambda: {"max_daily_realized_loss_usd": 200})
    block = await rail_max_daily_realized_loss(
        _proposal(),
        _account(realized_pnl_today_usd=Decimal("-300")),
    )
    assert block is not None


@pytest.mark.anyio
async def test_rail_stopless_runtime_assert_blocks_when_missing():
    from trading_sandwich.execution.policy_rails import rail_stopless_runtime_assert
    block = await rail_stopless_runtime_assert(_proposal(stop_loss=None), _account())
    assert block is not None


@pytest.mark.anyio
async def test_rail_execution_mode_gating_blocks_live_without_keys(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution.policy_rails import rail_execution_mode_gating
    monkeypatch.setattr(_policy, "get_execution_mode", lambda: "live")

    class _FakeSettings:
        binance_api_key = ""

    monkeypatch.setattr(
        "trading_sandwich.execution.policy_rails.get_settings",
        lambda: _FakeSettings(),
    )
    block = await rail_execution_mode_gating(_proposal(), _account())
    assert block is not None
    assert "live" in block


@pytest.mark.anyio
async def test_rail_first_trade_size_cap(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution import policy_rails
    monkeypatch.setattr(_policy, "get_max_order_usd", lambda: Decimal("500"))
    monkeypatch.setattr(
        _policy, "get_first_trade_size_multiplier", lambda: Decimal("0.5"),
    )
    monkeypatch.setattr(
        policy_rails, "_executed_today_count", AsyncMock(return_value=0),
    )
    block = await policy_rails.rail_first_trade_of_day_cap(
        _proposal(size_usd=Decimal("400")), _account(),
    )
    assert block is not None


@pytest.mark.anyio
async def test_evaluate_policy_returns_none_on_clean_proposal(monkeypatch):
    from trading_sandwich import _policy
    from trading_sandwich.execution import policy_rails

    monkeypatch.setattr(
        policy_rails, "_kill_switch_active", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(_policy, "is_trading_enabled", lambda: True)
    monkeypatch.setattr(_policy, "get_max_order_usd", lambda: Decimal("500"))
    monkeypatch.setattr(_policy, "get_universe_symbols", lambda: ["BTCUSDT"])
    monkeypatch.setattr(
        policy_rails, "_account_state", AsyncMock(return_value=_account()),
    )
    monkeypatch.setattr(
        policy_rails, "_executed_today_count", AsyncMock(return_value=5),
    )
    monkeypatch.setattr(
        policy_rails, "_open_positions_for_symbol", AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        policy_rails, "_open_positions_total", AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        policy_rails, "_correlated_total_usd", AsyncMock(return_value=Decimal("0")),
    )
    monkeypatch.setattr(
        _policy, "load_policy",
        lambda: {
            "max_leverage": 5,
            "max_open_positions_per_symbol": 1,
            "max_open_positions_total": 3,
            "max_daily_realized_loss_usd": 200,
            "max_orders_per_day": 20,
            "max_account_drawdown_pct": 10,
            "max_correlated_usd": 1000,
            "min_stop_distance_atr": 0.3,
            "max_stop_distance_atr": 5.0,
        },
    )
    block = await policy_rails.evaluate_policy(_proposal(size_usd=Decimal("400")))
    assert block is None
