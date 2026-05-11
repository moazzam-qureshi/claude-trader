"""Phase 3 Wave 1 Task 2.26 — backtest analytics unit tests.

compute_analytics() takes a list of equity points (USD value of
cash + position marked at that bar's close) and the trade count, and
returns: total_return_pct, num_trades, max_drawdown_pct, final_equity,
peak_equity, win_rate (fraction of *closed* trades that were
profitable — see note below).

win_rate here is computed from realised round trips: a sell that
brought in more USD than the matched buys cost. The replay engine
passes the realised-pnl-per-roundtrip list; analytics just summarises.
If there were no closed round trips, win_rate is None.
"""
from __future__ import annotations

from decimal import Decimal

from trading_sandwich.backtest.analytics import compute_analytics


def test_flat_equity_zero_return():
    a = compute_analytics(
        equity_curve=[Decimal("1000"), Decimal("1000"), Decimal("1000")],
        num_trades=0,
        roundtrip_pnls=[],
    )
    assert a["total_return_pct"] == Decimal("0")
    assert a["final_equity"] == Decimal("1000")
    assert a["peak_equity"] == Decimal("1000")
    assert a["max_drawdown_pct"] == Decimal("0")
    assert a["num_trades"] == 0
    assert a["win_rate"] is None


def test_monotonic_growth():
    a = compute_analytics(
        equity_curve=[Decimal("1000"), Decimal("1100"), Decimal("1210")],
        num_trades=4,
        roundtrip_pnls=[Decimal("100"), Decimal("110")],
    )
    # (1210 - 1000) / 1000 = 21%
    assert a["total_return_pct"] == Decimal("21")
    assert a["final_equity"] == Decimal("1210")
    assert a["peak_equity"] == Decimal("1210")
    assert a["max_drawdown_pct"] == Decimal("0")
    assert a["num_trades"] == 4
    assert a["win_rate"] == Decimal("1")  # both round trips positive


def test_drawdown_computed_from_peak():
    # rise to 1200, fall to 900, recover to 1000
    a = compute_analytics(
        equity_curve=[
            Decimal("1000"), Decimal("1200"), Decimal("900"), Decimal("1000"),
        ],
        num_trades=2,
        roundtrip_pnls=[Decimal("-50")],
    )
    # peak 1200, trough after peak 900 → drawdown (1200-900)/1200 = 25%
    assert a["peak_equity"] == Decimal("1200")
    assert a["max_drawdown_pct"] == Decimal("25")
    # total return (1000 - 1000)/1000 = 0
    assert a["total_return_pct"] == Decimal("0")
    assert a["win_rate"] == Decimal("0")  # the one round trip lost


def test_mixed_roundtrips_win_rate():
    a = compute_analytics(
        equity_curve=[Decimal("1000"), Decimal("1050")],
        num_trades=6,
        roundtrip_pnls=[
            Decimal("30"), Decimal("-10"), Decimal("40"), Decimal("-5"),
        ],
    )
    # 2 of 4 round trips positive → 0.5
    assert a["win_rate"] == Decimal("0.5")


def test_loss_overall():
    a = compute_analytics(
        equity_curve=[Decimal("1000"), Decimal("800")],
        num_trades=1,
        roundtrip_pnls=[],
    )
    assert a["total_return_pct"] == Decimal("-20")
    assert a["final_equity"] == Decimal("800")


def test_single_point_curve():
    a = compute_analytics(
        equity_curve=[Decimal("1000")],
        num_trades=0,
        roundtrip_pnls=[],
    )
    assert a["total_return_pct"] == Decimal("0")
    assert a["max_drawdown_pct"] == Decimal("0")


def test_empty_curve_raises():
    import pytest
    with pytest.raises(ValueError, match="equity_curve"):
        compute_analytics(equity_curve=[], num_trades=0, roundtrip_pnls=[])
