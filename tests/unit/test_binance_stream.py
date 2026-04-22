from datetime import UTC, datetime
from decimal import Decimal

from trading_sandwich.contracts.models import Candle
from trading_sandwich.ingestor.binance_stream import normalize_ccxt_ohlcv


def test_normalize_ccxt_ohlcv():
    raw = [1734480000000, 50000.0, 50100.0, 49990.0, 50050.0, 12.5]
    c = normalize_ccxt_ohlcv("BTCUSDT", "1m", raw)
    assert isinstance(c, Candle)
    assert c.symbol == "BTCUSDT"
    assert c.timeframe == "1m"
    assert c.open_time == datetime(2024, 12, 18, 0, 0, tzinfo=UTC)
    assert c.close_time == datetime(2024, 12, 18, 0, 1, tzinfo=UTC)
    assert c.open == Decimal("50000.0")
    assert c.close == Decimal("50050.0")
    assert c.volume == Decimal("12.5")


def test_normalize_ccxt_ohlcv_5m_close_time():
    raw = [1734480000000, 1.0, 2.0, 0.5, 1.5, 100.0]
    c = normalize_ccxt_ohlcv("ETHUSDT", "5m", raw)
    assert (c.close_time - c.open_time).total_seconds() == 300
