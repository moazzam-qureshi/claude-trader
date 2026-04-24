from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from trading_sandwich.ingestor.rest_poller import (
    fetch_funding_rate,
    fetch_long_short_ratio,
    fetch_open_interest,
)


class _MockTransport(httpx.MockTransport):
    """Preloaded JSON responses keyed by URL path."""
    def __init__(self, routes: dict[str, list]):
        def handler(request: httpx.Request) -> httpx.Response:
            body = routes[request.url.path]
            return httpx.Response(200, json=body)
        super().__init__(handler)


@pytest.mark.asyncio
async def test_fetch_funding_rate():
    transport = _MockTransport({
        "/fapi/v1/fundingRate": [
            {"symbol": "BTCUSDT", "fundingTime": 1734566400000, "fundingRate": "0.00012"},
            {"symbol": "BTCUSDT", "fundingTime": 1734595200000, "fundingRate": "0.00015"},
        ],
    })
    async with httpx.AsyncClient(transport=transport, base_url="https://fapi.binance.com") as client:
        rows = await fetch_funding_rate(client, symbol="BTCUSDT", limit=2)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["rate"] == Decimal("0.00012")
    assert isinstance(rows[0]["settlement_time"], datetime)
    assert rows[0]["settlement_time"].tzinfo == UTC


@pytest.mark.asyncio
async def test_fetch_open_interest():
    transport = _MockTransport({
        "/fapi/v1/openInterest": {
            "openInterest": "123456.789", "symbol": "BTCUSDT", "time": 1734595200000,
        },
    })
    async with httpx.AsyncClient(transport=transport, base_url="https://fapi.binance.com") as client:
        row = await fetch_open_interest(client, symbol="BTCUSDT", mark_price=Decimal("100000"))
    assert row["symbol"] == "BTCUSDT"
    assert row["open_interest_usd"] == Decimal("12345678900.000")
    assert row["captured_at"].tzinfo == UTC


@pytest.mark.asyncio
async def test_fetch_long_short_ratio():
    transport = _MockTransport({
        "/futures/data/topLongShortAccountRatio": [
            {"symbol": "BTCUSDT", "longShortRatio": "1.5", "timestamp": 1734595200000,
             "longAccount": "0.6", "shortAccount": "0.4"},
        ],
    })
    async with httpx.AsyncClient(transport=transport, base_url="https://fapi.binance.com") as client:
        rows = await fetch_long_short_ratio(client, symbol="BTCUSDT", period="5m", limit=1)
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["ratio"] == Decimal("1.5")
