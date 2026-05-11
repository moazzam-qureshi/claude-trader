"""CCXTSpotAdapter — Binance plain-spot adapter (halal: no margin/short).

Focus: the adapter must honour OrderRequest.direction. A 'buy' adds to
the long (and, if it carries a real protective stop, attaches a
stop_loss_limit sell); a 'sell' liquidates held inventory via a CCXT
sell order and attaches no stop. A position-side 'short' is still a
hard rejection (you cannot sell what you do not own on spot).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading_sandwich.contracts.phase2 import OrderRequest, StopLossSpec


class _FakeExchange:
    def __init__(self):
        self.created: list[dict] = []

    def set_sandbox_mode(self, _flag):  # noqa: D401
        pass

    async def fetch_ticker(self, _symbol):
        return {"last": 80000.0}

    async def create_order(self, *, symbol, type, side, amount, price, params):
        self.created.append({
            "symbol": symbol, "type": type, "side": side,
            "amount": amount, "price": price, "params": params,
        })
        return {"id": f"oid-{len(self.created)}", "status": "open",
                "average": None, "filled": None}


def _adapter_with_fake(monkeypatch):
    from trading_sandwich.execution.adapters import ccxt_spot

    fake = _FakeExchange()

    class _Spot(ccxt_spot.CCXTSpotAdapter):
        def __init__(self):
            self._exchange = fake

    return _Spot(), fake


def _req(**over):
    base = dict(
        symbol="BTCUSDT", side="long", order_type="limit",
        size_usd=Decimal("12"), limit_price=Decimal("80000"),
        stop_loss=StopLossSpec(kind="structural", value=Decimal("0")),
        client_order_id="c-1",
    )
    base.update(over)
    return OrderRequest(**base)


@pytest.mark.anyio
async def test_buy_with_noop_stop_places_only_the_buy(monkeypatch):
    adapter, fake = _adapter_with_fake(monkeypatch)
    r = await adapter.submit_order(_req(direction="buy"))
    assert r.status == "open"
    # one create_order — the buy. No stop attached (structural value 0).
    assert len(fake.created) == 1
    assert fake.created[0]["side"] == "buy"
    assert fake.created[0]["type"] == "limit"


@pytest.mark.anyio
async def test_sell_intent_submits_a_ccxt_sell_no_stop(monkeypatch):
    adapter, fake = _adapter_with_fake(monkeypatch)
    r = await adapter.submit_order(_req(direction="sell", client_order_id="c-sell"))
    assert r.status == "open"
    assert len(fake.created) == 1
    assert fake.created[0]["side"] == "sell"  # not 'buy'!
    assert fake.created[0]["symbol"] == "BTCUSDT"


@pytest.mark.anyio
async def test_buy_with_real_stop_attaches_stop_loss_limit(monkeypatch):
    adapter, fake = _adapter_with_fake(monkeypatch)
    await adapter.submit_order(_req(
        direction="buy",
        stop_loss=StopLossSpec(kind="fixed_price", value=Decimal("75000")),
    ))
    # two create_order calls: the buy, then the protective sell stop
    assert len(fake.created) == 2
    assert fake.created[0]["side"] == "buy"
    assert fake.created[1]["side"] == "sell"
    assert fake.created[1]["type"] == "stop_loss_limit"
    assert fake.created[1]["params"]["stopPrice"] == 75000.0


@pytest.mark.anyio
async def test_position_side_short_is_hard_rejected(monkeypatch):
    adapter, fake = _adapter_with_fake(monkeypatch)
    r = await adapter.submit_order(_req(side="short"))
    assert r.status == "rejected"
    assert "halal_spot_no_shorts" in (r.rejection_reason or "")
    assert fake.created == []  # no Binance call at all
