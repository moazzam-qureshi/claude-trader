"""Phase 3 Wave 1 Task 2.26 — backtest replay engine unit tests.

replay_strategy() walks a candle series, builds a StrategyContext +
snapshot for each bar via a snapshot_builder callback, calls
strategy.tick(), runs the emitted OrderIntents through the fill
simulator against that bar's candle, updates a cash/position book,
and records an equity point per bar. Returns a BacktestResult with
the equity curve, the fills, the realised round-trip PnLs, and the
analytics dict.

State persists between ticks in-memory (a plain dict), exactly as the
worker persists it via the DB. The snapshot_builder is supplied by
the caller so the engine doesn't need to know each strategy's data
needs; a default_price_snapshot_builder covering mid_price / now /
reference_price / ATR / EMA / RSI / Bollinger is provided for the
common case.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_sandwich.backtest.fill_sim import Candle
from trading_sandwich.backtest.replay import replay_strategy
from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)


_T0 = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


def _candles(closes: list[str], *, span=timedelta(hours=1)) -> list[Candle]:
    out: list[Candle] = []
    for i, c in enumerate(closes):
        cl = Decimal(c)
        out.append(Candle(
            open_time=_T0 + span * i,
            open=cl, high=cl, low=cl, close=cl, volume=Decimal("1"),
        ))
    return out


class _BuyOnceStrategy(Strategy):
    """Buys a fixed USD market order on the first tick, then holds."""

    def tick(self, ctx, snapshot):
        if ctx.state.get("bought"):
            return []
        ctx.state["bought"] = True
        return [OrderIntent(
            symbol=ctx.symbol, order_type="market",
            size_usd=Decimal("100"),
            client_order_id=f"buy1-{ctx.strategy_id}", role="entry",
        )]

    def graceful_shutdown(self, ctx): return []
    def emergency_stop(self, ctx): return []
    def expected_return_for_regime(self, regime):
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.5)


class _BuyThenSellStrategy(Strategy):
    """Buy 100 on tick 0; sell the whole position on tick 2."""

    def tick(self, ctx, snapshot):
        n = int(ctx.state.get("n", 0))
        ctx.state["n"] = n + 1
        if n == 0:
            return [OrderIntent(
                symbol=ctx.symbol, order_type="market",
                size_usd=Decimal("100"),
                client_order_id=f"b-{ctx.strategy_id}", role="entry",
            )]
        if n == 2 and not ctx.state.get("sold"):
            ctx.state["sold"] = True
            # sell the held units at market: size_usd is current value
            units = Decimal(ctx.state.get("units_hint", "0"))
            if units > 0:
                return [OrderIntent(
                    symbol=ctx.symbol, order_type="market",
                    size_usd=units,  # interpreted as USD-equivalent below
                    client_order_id=f"s-{ctx.strategy_id}", role="exit",
                )]
        return []

    def graceful_shutdown(self, ctx): return []
    def emergency_stop(self, ctx): return []
    def expected_return_for_regime(self, regime):
        return ReturnExpectation(monthly_return_pct=Decimal("0"), confidence=0.5)


def _trivial_snapshot(candle, prior, state):
    return {"mid_price": candle.close, "now": candle.open_time}


# ---------- Basic replay ----------


def test_buy_once_replay_holds_position():
    """Buy 100 USD at price 100 on bar 0 (qty 1.0), then hold while
    price climbs to 120. Equity = cash + position_value should end at
    initial_capital - 100 (spent) + 1.0*120 = 900 + 120 = 1020 (zero
    fees/slippage)."""
    candles = _candles(["100", "110", "120"])
    result = replay_strategy(
        strategy=_BuyOnceStrategy(),
        candles=candles,
        params={},
        initial_capital_usd=Decimal("1000"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        snapshot_builder=_trivial_snapshot,
    )
    assert len(result.equity_curve) == 3
    # bar 0: bought 100 USD at 100 → qty 1.0, cash 900, pos 1.0*100=100 → equity 1000
    assert result.equity_curve[0] == Decimal("1000")
    # bar 1: price 110 → pos 110, cash 900 → equity 1010
    assert result.equity_curve[1] == Decimal("1010")
    # bar 2: price 120 → pos 120, cash 900 → equity 1020
    assert result.equity_curve[2] == Decimal("1020")
    assert len(result.fills) == 1
    assert result.fills[0].side == "buy"
    assert result.analytics["total_return_pct"] == Decimal("2")
    assert result.analytics["num_trades"] == 1


def test_replay_applies_fees_and_slippage():
    """Buy 100 USD at price 100 with 10bps fee, 0 slippage on a market
    buy → spends 100 + 0.10 fee = 100.10 cash; qty = 100 / 100 = 1.0.
    (slippage 0 here for a clean number.)"""
    candles = _candles(["100", "100"])
    result = replay_strategy(
        strategy=_BuyOnceStrategy(),
        candles=candles,
        params={},
        initial_capital_usd=Decimal("1000"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        snapshot_builder=_trivial_snapshot,
    )
    # bar 0: cash = 1000 - 100.10 = 899.90; pos = 1.0 * 100 = 100 → equity 999.90
    assert result.equity_curve[0] == Decimal("999.90")
    assert result.fills[0].fee_usd == Decimal("0.10")


def test_replay_empty_candles_raises():
    import pytest
    with pytest.raises(ValueError, match="candles"):
        replay_strategy(
            strategy=_BuyOnceStrategy(), candles=[], params={},
            initial_capital_usd=Decimal("1000"),
            fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            snapshot_builder=_trivial_snapshot,
        )


def test_replay_state_persists_between_ticks():
    """The strategy's ctx.state survives across bars (the engine threads
    it through). _BuyOnceStrategy relies on state['bought'] to fire
    exactly once — if state weren't persisted it would buy every bar."""
    candles = _candles(["100", "100", "100", "100", "100"])
    result = replay_strategy(
        strategy=_BuyOnceStrategy(),
        candles=candles, params={},
        initial_capital_usd=Decimal("1000"),
        fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
        snapshot_builder=_trivial_snapshot,
    )
    assert len(result.fills) == 1  # only one buy across 5 bars


# ---------- default_price_snapshot_builder ----------


def test_default_snapshot_builder_provides_common_fields():
    from trading_sandwich.backtest.replay import default_price_snapshot_builder

    candles = _candles(["100", "101", "102", "103", "104", "105",
                        "106", "107", "108", "109", "110", "111",
                        "112", "113", "114", "115", "116", "117",
                        "118", "119", "120", "121", "122", "123"])
    # Build a snapshot at the last bar with 23 prior bars of history.
    snap = default_price_snapshot_builder(candles[-1], candles[:-1], {})
    assert snap["mid_price"] == Decimal("123")
    assert "now" in snap
    # reference_price = the prior bar's close
    assert snap["reference_price"] == Decimal("122")
    # ATR-family + EMA + RSI fields present (values not pinned — just
    # that the builder produces them so a strategy needing them can run)
    for key in ("atr", "atr_pct", "ma_fast", "ma_slow", "ma_n", "rsi",
                "bb_lower", "bb_upper"):
        assert key in snap, f"default snapshot missing {key}"


def test_default_snapshot_builder_short_history_still_works():
    """With too little history for the indicator windows, the builder
    must still return a dict (with whatever it can compute or sane
    fallbacks) rather than crashing — the replay should degrade, not
    explode, on the warm-up bars."""
    from trading_sandwich.backtest.replay import default_price_snapshot_builder

    candles = _candles(["100", "101"])
    snap = default_price_snapshot_builder(candles[-1], candles[:-1], {})
    assert snap["mid_price"] == Decimal("101")
    assert "now" in snap
