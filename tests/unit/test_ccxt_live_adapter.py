def test_ccxt_live_adapter_implements_abstract_methods():
    from trading_sandwich.execution.adapters.ccxt_live import CCXTProAdapter
    from trading_sandwich.execution.adapters.base import ExchangeAdapter
    assert issubclass(CCXTProAdapter, ExchangeAdapter)
