"""Async Binance USD-M futures REST fetchers. Returns normalized dicts ready
for INSERT into raw_funding / raw_open_interest / raw_long_short_ratio.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

_FAPI_BASE = "https://fapi.binance.com"


async def fetch_funding_rate(
    client: httpx.AsyncClient, *, symbol: str, limit: int = 100,
) -> list[dict]:
    """GET /fapi/v1/fundingRate?symbol=<symbol>&limit=<limit>.
    Returns rows sorted by settlement_time ascending.
    """
    resp = await client.get("/fapi/v1/fundingRate", params={"symbol": symbol, "limit": limit})
    resp.raise_for_status()
    rows = [
        {
            "symbol": r["symbol"],
            "settlement_time": datetime.fromtimestamp(r["fundingTime"] / 1000, tz=UTC),
            "rate": Decimal(str(r["fundingRate"])),
        }
        for r in resp.json()
    ]
    rows.sort(key=lambda x: x["settlement_time"])
    return rows


async def fetch_open_interest(
    client: httpx.AsyncClient, *, symbol: str, mark_price: Decimal,
) -> dict:
    """GET /fapi/v1/openInterest?symbol=<symbol>.
    Multiplies contracts by `mark_price` to store USD value.
    """
    resp = await client.get("/fapi/v1/openInterest", params={"symbol": symbol})
    resp.raise_for_status()
    data = resp.json()
    contracts = Decimal(str(data["openInterest"]))
    return {
        "symbol": data["symbol"],
        "captured_at": datetime.fromtimestamp(data["time"] / 1000, tz=UTC),
        "open_interest_usd": (contracts * mark_price).quantize(Decimal("0.001")),
    }


async def fetch_long_short_ratio(
    client: httpx.AsyncClient, *, symbol: str, period: str = "5m", limit: int = 30,
) -> list[dict]:
    """GET /futures/data/topLongShortAccountRatio?symbol=<symbol>&period=<period>&limit=<limit>."""
    resp = await client.get(
        "/futures/data/topLongShortAccountRatio",
        params={"symbol": symbol, "period": period, "limit": limit},
    )
    resp.raise_for_status()
    rows = [
        {
            "symbol": r["symbol"],
            "captured_at": datetime.fromtimestamp(r["timestamp"] / 1000, tz=UTC),
            "ratio": Decimal(str(r["longShortRatio"])),
        }
        for r in resp.json()
    ]
    rows.sort(key=lambda x: x["captured_at"])
    return rows


def fapi_base_url() -> str:
    """Exposed as a helper so beat jobs can build one `httpx.AsyncClient` per invocation."""
    return _FAPI_BASE
