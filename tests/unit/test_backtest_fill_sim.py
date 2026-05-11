"""Phase 3 Wave 1 Task 2.26 — backtest fill simulator unit tests.

The fill simulator turns one OrderIntent + one candle into a Fill (or
None if it wouldn't fill that bar). Models:

  market buy  → fills at close * (1 + slippage_bps/10000), fee deducted
  market sell → fills at close * (1 - slippage_bps/10000), fee deducted
  limit buy   → fills iff candle.low  <= limit_price (at limit_price)
  limit sell  → fills iff candle.high >= limit_price (at limit_price)

Fills are denominated in base units: qty = filled_usd / fill_price,
and the fee (in USD) reduces the cash impact. The Fill carries
enough to update a position book: side, role, fill_price, qty,
fee_usd, gross_usd, net_usd. The trade direction (`side` on the Fill)
comes straight from OrderIntent.direction — role is an audit
passthrough only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_sandwich.backtest.fill_sim import Candle, simulate_fill
from trading_sandwich.strategies.base import OrderIntent


_T = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


def _candle(o="100", h="110", lo="90", c="105", v="1000") -> Candle:
    return Candle(
        open_time=_T,
        open=Decimal(o), high=Decimal(h), low=Decimal(lo),
        close=Decimal(c), volume=Decimal(v),
    )


# ---------- Market orders ----------


def test_market_buy_fills_at_close_plus_slippage_minus_fee():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="market", size_usd=Decimal("100"),
        client_order_id="x-1", role="entry",
    )
    fill = simulate_fill(
        intent, _candle(c="100"), fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
    )
    assert fill is not None
    # close 100 + 20bps = 100.20
    assert fill.fill_price == Decimal("100.20")
    assert fill.side == "buy"
    assert fill.role == "entry"
    # gross_usd is the requested 100; qty = 100 / 100.20
    assert fill.gross_usd == Decimal("100")
    assert fill.qty == Decimal("100") / Decimal("100.20")
    # fee 10bps of 100 = 0.10; a buy spends gross + fee
    assert fill.fee_usd == Decimal("0.10")
    assert fill.net_usd == Decimal("-100.10")  # cash out


def test_market_sell_fills_at_close_minus_slippage_minus_fee():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="market", size_usd=Decimal("100"),
        client_order_id="x-2", role="exit", direction="sell",
    )
    fill = simulate_fill(
        intent, _candle(c="100"), fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
    )
    assert fill is not None
    # close 100 - 20bps = 99.80
    assert fill.fill_price == Decimal("99.80")
    assert fill.side == "sell"
    assert fill.gross_usd == Decimal("100")
    assert fill.qty == Decimal("100") / Decimal("99.80")
    assert fill.fee_usd == Decimal("0.10")
    # a sell brings in gross - fee
    assert fill.net_usd == Decimal("99.90")  # cash in


def test_zero_slippage_zero_fee():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="market", size_usd=Decimal("50"),
        client_order_id="x-3", role="entry",
    )
    fill = simulate_fill(
        intent, _candle(c="200"), fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    assert fill is not None
    assert fill.fill_price == Decimal("200")
    assert fill.qty == Decimal("0.25")
    assert fill.fee_usd == Decimal("0")
    assert fill.net_usd == Decimal("-50")


# ---------- Limit buy ----------


def test_limit_buy_fills_when_low_touches_limit():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="limit", size_usd=Decimal("100"),
        limit_price=Decimal("95"), client_order_id="x-4", role="entry",
    )
    # candle low 90 <= 95 → fills at 95
    fill = simulate_fill(
        intent, _candle(lo="90", h="110", c="105"), fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
    )
    assert fill is not None
    assert fill.fill_price == Decimal("95")  # limit, no slippage on a limit
    assert fill.side == "buy"
    assert fill.qty == Decimal("100") / Decimal("95")
    assert fill.fee_usd == Decimal("0.10")


def test_limit_buy_does_not_fill_when_low_above_limit():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="limit", size_usd=Decimal("100"),
        limit_price=Decimal("85"), client_order_id="x-5", role="entry",
    )
    # candle low 90 > 85 → no fill
    fill = simulate_fill(
        intent, _candle(lo="90"), fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
    )
    assert fill is None


def test_limit_buy_fills_exactly_at_low_equals_limit():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="limit", size_usd=Decimal("100"),
        limit_price=Decimal("90"), client_order_id="x-6", role="entry",
    )
    fill = simulate_fill(
        intent, _candle(lo="90"), fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    assert fill is not None
    assert fill.fill_price == Decimal("90")


# ---------- Limit sell ----------


def test_limit_sell_fills_when_high_touches_limit():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="limit", size_usd=Decimal("100"),
        limit_price=Decimal("108"), client_order_id="x-7", role="exit",
        direction="sell",
    )
    # candle high 110 >= 108 → fills at 108
    fill = simulate_fill(
        intent, _candle(h="110", lo="90", c="105"), fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
    )
    assert fill is not None
    assert fill.fill_price == Decimal("108")
    assert fill.side == "sell"
    assert fill.qty == Decimal("100") / Decimal("108")


def test_limit_sell_does_not_fill_when_high_below_limit():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="limit", size_usd=Decimal("100"),
        limit_price=Decimal("120"), client_order_id="x-8", role="exit",
        direction="sell",
    )
    fill = simulate_fill(
        intent, _candle(h="110"), fee_bps=Decimal("10"),
        slippage_bps=Decimal("20"),
    )
    assert fill is None


# ---------- direction-derived side ----------
#
# The simulator takes the trade direction straight from
# OrderIntent.direction ('buy' | 'sell'). role is carried onto the Fill
# only as an audit passthrough. This closes the old rebalance-as-buy
# limitation: a rebalance-trim emits direction='sell' and is modelled
# as a sell.


def test_side_comes_from_direction_not_role():
    # A 'rebalance' role with direction='sell' fills as a sell (the
    # rebalance-trim case the old role-inference got wrong).
    trim = OrderIntent(
        symbol="BTCUSDT", order_type="market", size_usd=Decimal("10"),
        client_order_id="rb-trim", role="rebalance", direction="sell",
    )
    fill = simulate_fill(trim, _candle(c="100"),
                         fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert fill.side == "sell"
    assert fill.role == "rebalance"  # audit passthrough unchanged

    # A 'rebalance' role with the default direction is a buy.
    add = OrderIntent(
        symbol="BTCUSDT", order_type="market", size_usd=Decimal("10"),
        client_order_id="rb-add", role="rebalance",
    )
    fill = simulate_fill(add, _candle(c="100"),
                         fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert fill.side == "buy"


def test_default_direction_is_buy():
    for role in ("entry", "rebalance", "take_profit", "stop_loss"):
        intent = OrderIntent(
            symbol="BTCUSDT", order_type="market", size_usd=Decimal("10"),
            client_order_id=f"r-{role}", role=role,
        )
        fill = simulate_fill(intent, _candle(c="100"),
                             fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
        assert fill.side == "buy"


# ---------- Validation ----------


def test_limit_order_without_limit_price_raises():
    intent = OrderIntent(
        symbol="BTCUSDT", order_type="limit", size_usd=Decimal("100"),
        client_order_id="x-9", role="entry",
    )
    with pytest.raises(ValueError, match="limit_price"):
        simulate_fill(intent, _candle(), fee_bps=Decimal("0"),
                      slippage_bps=Decimal("0"))
