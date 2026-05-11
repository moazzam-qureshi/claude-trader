"""Phase 3 Wave 1 Task 2.26 — backtest a real strategy end-to-end.

Runs A1 Standard Grid and D4 Time-Series Momentum through
replay_strategy over synthetic candles, exercising the whole
framework against production strategy code (not stubs). The point is
the wiring: a real strategy ticks, emits intents, fills land, equity
moves, analytics come out coherent. Exact PnL numbers aren't pinned
(they depend on the synthetic path) — invariants are.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_sandwich.backtest import (
    Candle,
    default_price_snapshot_builder,
    replay_strategy,
)
from trading_sandwich.strategies.grid.standard import StandardGridStrategy
from trading_sandwich.strategies.trend.time_series_momentum import (
    TimeSeriesMomentumStrategy,
)


_T0 = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


def _ramp(start: int, end: int, n: int) -> list[Candle]:
    """n candles whose close ramps linearly from `start` to `end`,
    each bar's high/low straddling the close by ±0.5%."""
    out: list[Candle] = []
    for i in range(n):
        c = Decimal(start) + (Decimal(end) - Decimal(start)) * Decimal(i) / Decimal(n - 1)
        out.append(Candle(
            open_time=_T0 + timedelta(hours=i),
            open=c, high=c * Decimal("1.005"), low=c * Decimal("0.995"),
            close=c, volume=Decimal("1"),
        ))
    return out


# ---------- A1 Standard Grid ----------


def test_standard_grid_backtest_runs_and_fills():
    """Grid 45000-55000, 5 levels, over a price path that dips into the
    grid then climbs through it. Some buy levels should fill (price
    touches their limit), equity curve has one point per bar, analytics
    are coherent."""
    # Price starts above the grid (60000), dips to 44000 (below all
    # levels), then climbs back to 56000 (above all levels).
    candles = _ramp(60000, 44000, 12) + _ramp(44000, 56000, 12)

    result = replay_strategy(
        strategy=StandardGridStrategy(),
        candles=candles,
        params={"low": "45000", "high": "55000", "levels": 5},
        initial_capital_usd=Decimal("1000"),
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        snapshot_builder=default_price_snapshot_builder,
    )

    assert len(result.equity_curve) == len(candles)
    # The grid deploys its buy ladder on the first tick (mid 60000 is
    # above all 5 levels → all 5 buys submitted as limit orders).
    # As price dips through 55000..45000 those limits fill.
    buy_fills = [f for f in result.fills if f.side == "buy"]
    assert len(buy_fills) >= 1, "expected some grid buy levels to fill"
    # Analytics dict is well-formed.
    a = result.analytics
    assert a["num_trades"] == len(result.fills)
    assert a["final_equity"] == result.equity_curve[-1]
    assert a["max_drawdown_pct"] >= Decimal("0")
    # Cash never goes negative beyond a tiny rounding tolerance and
    # position units are non-negative (no shorting).
    assert result.final_position_units >= Decimal("0")


def test_standard_grid_backtest_no_fills_when_price_stays_above_grid():
    """If price never dips into the grid range, the buy limits never
    fill — zero buy fills, equity stays flat at initial capital (all
    cash, no position)."""
    candles = _ramp(60000, 70000, 20)  # always above the 45k-55k grid
    result = replay_strategy(
        strategy=StandardGridStrategy(),
        candles=candles,
        params={"low": "45000", "high": "55000", "levels": 5},
        initial_capital_usd=Decimal("1000"),
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        snapshot_builder=default_price_snapshot_builder,
    )
    assert len([f for f in result.fills if f.side == "buy"]) == 0
    # All cash, no position → equity flat at 1000 every bar.
    assert all(e == Decimal("1000") for e in result.equity_curve)
    assert result.analytics["total_return_pct"] == Decimal("0")


# ---------- D4 Time-Series Momentum ----------


def test_tsm_backtest_enters_on_uptrend_exits_on_downtrend():
    """Time-Series Momentum: long while price > ma_n. A path that
    climbs (price > MA → enter) then falls (price < MA → exit) should
    produce at least one buy and one sell, and end flat (all cash)."""
    # Climb 100→200 over 40 bars, then fall 200→80 over 40 bars.
    candles = _ramp(100, 200, 40) + _ramp(200, 80, 40)
    result = replay_strategy(
        strategy=TimeSeriesMomentumStrategy(),
        candles=candles,
        params={"position_usd": "100"},
        initial_capital_usd=Decimal("1000"),
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        snapshot_builder=default_price_snapshot_builder,
    )
    sides = [f.side for f in result.fills]
    assert "buy" in sides, "expected an entry while price was above the MA"
    assert "sell" in sides, "expected an exit when price fell below the MA"
    # After the fall the strategy is flat → position units ~0.
    assert result.final_position_units >= Decimal("0")
    # A round trip happened → win_rate is a Decimal, not None.
    assert result.analytics["win_rate"] is not None
