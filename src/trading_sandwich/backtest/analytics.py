"""Backtest analytics — Phase 3 Wave 1 Task 2.26.

Pure summary of a backtest run: an equity curve (USD value of cash +
position marked at each bar's close), the trade count, and the list
of realised round-trip PnLs (one entry per closed buy→sell cycle the
replay engine matched). Returns a metrics dict:

  total_return_pct  — (final - initial) / initial * 100
  num_trades        — fills count (buys + sells)
  max_drawdown_pct  — worst peak-to-trough decline on the curve, %
  final_equity      — last point
  peak_equity       — highest point
  win_rate          — fraction of round trips with positive PnL,
                      or None if there were no closed round trips
"""
from __future__ import annotations

from decimal import Decimal


def compute_analytics(
    *,
    equity_curve: list[Decimal],
    num_trades: int,
    roundtrip_pnls: list[Decimal],
) -> dict:
    if not equity_curve:
        raise ValueError("equity_curve must be non-empty")

    initial = equity_curve[0]
    final = equity_curve[-1]
    total_return_pct = (
        (final - initial) / initial * Decimal("100")
        if initial != Decimal("0")
        else Decimal("0")
    )

    peak = equity_curve[0]
    max_dd = Decimal("0")
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > Decimal("0"):
            dd = (peak - v) / peak * Decimal("100")
            if dd > max_dd:
                max_dd = dd

    if roundtrip_pnls:
        wins = sum(1 for p in roundtrip_pnls if p > Decimal("0"))
        win_rate = Decimal(wins) / Decimal(len(roundtrip_pnls))
    else:
        win_rate = None

    return {
        "total_return_pct": total_return_pct,
        "num_trades": num_trades,
        "max_drawdown_pct": max_dd,
        "final_equity": final,
        "peak_equity": peak,
        "win_rate": win_rate,
    }
