"""One-shot Discord webhook poster for alerts and proposal cards."""
from __future__ import annotations

import httpx


async def post_webhook(url: str, payload: dict, *, timeout_s: float = 10.0) -> int:
    """POST a JSON payload to a Discord webhook. Returns HTTP status code."""
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(url, json=payload)
    return r.status_code
