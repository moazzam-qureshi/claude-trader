"""A7 Z-Score Reversion — Phase 3 Wave 1 Task 2.7.

Mechanic: read price z-score from snapshot.
  z < -entry_threshold (default -2.0): emit buy at mid (entry).
  z > +exit_threshold (default +2.0) AND position > 0: emit sell at
    mid (exit) sized to current position.

Hysteresis: last_signal_kind ∈ {'low', 'high', 'middle'} dedupes
consecutive same-bucket reads.

Halal-spot inviolable: every emitted intent has side='long'. Sells
only happen when position > 0.

Snapshot contract: {'mid_price', 'price_z_score'}. The features stack
does not yet emit a price z-score — only volume_zscore_20. A
supporting Wave 1 task adds price_zscore_20 to features/compute.py.
Until then this strategy can run with manually-fed snapshots in
backtest mode but won't fire in production.

Asymmetric thresholds supported: entry_threshold (negative breach
sigma) and exit_threshold (positive breach sigma) can differ. Both
are stored as positive Decimals; the negative side is implicit from
the comparison direction.

Spec §6.2 compat: [RANGE_VOLATILE, RANGE_QUIET].

After A5/A6/A7 the classify-into-buckets + hysteresis + position-track
pattern is now in three places. The next commit lifts a shared helper
into strategies/mean_reversion/_base.py.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)
from trading_sandwich.strategies.mean_reversion._base import apply_signal


_COID_PREFIX = "zscore"


def _read_params(params: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    try:
        entry_t = Decimal(str(params["entry_threshold"]))
        exit_t = Decimal(str(params["exit_threshold"]))
        entry_size = Decimal(str(params["entry_size_usd"]))
    except KeyError as e:
        raise KeyError(
            f"z_score_reversion params missing required key: {e}"
        ) from e
    if entry_t <= Decimal("0") or exit_t <= Decimal("0"):
        raise ValueError(
            f"thresholds must be > 0 (positive sigma); got entry={entry_t} exit={exit_t}"
        )
    return entry_t, exit_t, entry_size


def _classify(
    z: Decimal, entry_t: Decimal, exit_t: Decimal,
) -> str:
    if z < -entry_t:
        return "low"
    if z > exit_t:
        return "high"
    return "middle"


class ZScoreReversionStrategy(Strategy):
    """A7 Z-Score Reversion."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("mid_price", "price_z_score"):
            if k not in snapshot:
                raise KeyError(f"z_score_reversion requires snapshot[{k!r}]")
        mid = Decimal(str(snapshot["mid_price"]))
        z = Decimal(str(snapshot["price_z_score"]))
        entry_t, exit_t, entry_size = _read_params(ctx.params)

        kind = _classify(z, entry_t, exit_t)
        return apply_signal(
            ctx=ctx,
            kind=kind,
            entry_kind_name="low",
            exit_kind_name="high",
            mid=mid,
            entry_size=entry_size,
            coid_prefix=_COID_PREFIX,
        )

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        match regime:
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.035"),
                    confidence=0.55,
                    rationale="Statistical extremes revert in stable mean",
                )
            case Regime.RANGE_QUIET:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.018"),
                    confidence=0.5,
                    rationale="Tight z-distribution, fewer signals",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0"),
                    confidence=0.7,
                    rationale="Out-of-regime: trends break the mean",
                )
