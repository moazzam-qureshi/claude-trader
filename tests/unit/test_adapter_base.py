import pytest


def test_exchange_adapter_is_abstract():
    from trading_sandwich.execution.adapters.base import ExchangeAdapter

    with pytest.raises(TypeError):
        ExchangeAdapter()  # type: ignore[abstract]


def test_exchange_adapter_required_methods():
    from trading_sandwich.execution.adapters.base import ExchangeAdapter
    abstract_methods = ExchangeAdapter.__abstractmethods__
    assert "submit_order" in abstract_methods
    assert "cancel_order" in abstract_methods
    assert "get_open_orders" in abstract_methods
    assert "get_positions" in abstract_methods
    assert "get_account_state" in abstract_methods
