from datetime import UTC, datetime

from trading_sandwich.ingestor.binance_depth_stream import normalize_ccxt_depth


def test_normalize_ccxt_depth():
    raw = {
        "symbol": "BTC/USDT",
        "bids": [["99.8", "10"], ["99.5", "7"]],
        "asks": [["100.2", "5"], ["100.5", "12"]],
        "timestamp": 1734595200000,
    }
    snap = normalize_ccxt_depth("BTCUSDT", raw)
    assert snap["symbol"] == "BTCUSDT"
    assert snap["captured_at"] == datetime.fromtimestamp(1734595200.0, tz=UTC)
    assert snap["bids"][0] == ["99.8", "10"]
    assert snap["asks"][0] == ["100.2", "5"]


def test_normalize_ccxt_depth_uses_now_if_no_timestamp():
    raw = {"symbol": "BTC/USDT", "bids": [], "asks": [], "timestamp": None}
    snap = normalize_ccxt_depth("BTCUSDT", raw)
    assert abs((datetime.now(UTC) - snap["captured_at"]).total_seconds()) < 5
