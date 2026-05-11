"""F1 Halving Cycle Positioning — Phase 3 Wave 1 Task 2.24.

Mechanical capital deployment by Bitcoin halving-cycle phase. The
~4-year cycle (1458 days) splits into four phases measured from the
last halving date:

  accumulation : days   0 ..  182  (≈ 0–6 months post-halving)
  bull         : days 183 ..  547  (≈ 6–18 months)
  distribution : days 548 ..  730  (≈ 18–24 months)
  bear         : days 731 .. 1457  (≈ 24–48 months)

Each phase carries a target position fraction; the strategy
rebalances toward (phase_fraction * capital) on a slow cadence.
Multi-cycle: days_since_halving is taken mod 1458, so one
last_halving_date covers all future cycles.

  target_value = phase_fractions[phase(days_since_halving % 1458)] * capital
  then close the gap to actual position value (rebalance/_base.py)

Halal-spot inviolable: side='long' on every intent. The trim-down's
sell value caps at the held value — never goes short. Position units
estimated as size_usd / mid on a buy; fill-delivery plumbing corrects
later. Reuses rebalance/_base.py's rebalance_toward_value().

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal}.
State: position_units, rebalance_count, last_rebalance_at (iso).

Spec §6.2 compat: ["*"].
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from trading_sandwich.strategies.base import (
    OrderIntent,
    Regime,
    ReturnExpectation,
    Strategy,
    StrategyContext,
)
from trading_sandwich.strategies.rebalance._base import rebalance_toward_value


_COID_PREFIX = "cychlv"
_CYCLE_DAYS = 1458
_PHASE_BOUNDS = (  # (phase_name, last_day_inclusive)
    ("accumulation", 182),
    ("bull", 547),
    ("distribution", 730),
    ("bear", _CYCLE_DAYS - 1),
)
_REQUIRED_PHASES = {name for name, _ in _PHASE_BOUNDS}


def _phase_for_day(day_in_cycle: int) -> str:
    for name, last in _PHASE_BOUNDS:
        if day_in_cycle <= last:
            return name
    return "bear"  # unreachable — last bound is _CYCLE_DAYS - 1


def _read_params(
    params: dict[str, Any],
) -> tuple[date, dict[str, Decimal], int]:
    try:
        last_halving = date.fromisoformat(str(params["last_halving_date"]))
        raw_fractions = dict(params["phase_fractions"])
        interval = int(params["interval_seconds"])
    except KeyError as e:
        raise KeyError(
            f"cycle_halving params missing required key: {e}"
        ) from e
    missing = _REQUIRED_PHASES - set(raw_fractions)
    if missing:
        raise ValueError(
            f"phase_fractions missing phases: {sorted(missing)}"
        )
    fractions: dict[str, Decimal] = {}
    for name in _REQUIRED_PHASES:
        frac = Decimal(str(raw_fractions[name]))
        if frac < Decimal("0") or frac > Decimal("1"):
            raise ValueError(
                f"phase_fractions[{name!r}] must be in [0, 1], got {frac}"
            )
        fractions[name] = frac
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    return last_halving, fractions, interval


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class HalvingCyclePositioningStrategy(Strategy):
    """F1 Halving Cycle Positioning — fraction by cycle phase."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "mid_price"):
            if k not in snapshot:
                raise KeyError(f"cycle_halving requires snapshot[{k!r}]")
        now = _require_aware(snapshot["now"])
        mid = Decimal(str(snapshot["mid_price"]))
        last_halving, fractions, interval = _read_params(ctx.params)

        days_since = (now.astimezone(timezone.utc).date() - last_halving).days
        if days_since < 0:
            raise ValueError(
                f"snapshot['now'] ({now.date()}) is before last_halving_date "
                f"({last_halving})"
            )
        phase = _phase_for_day(days_since % _CYCLE_DAYS)

        last_iso = ctx.state.get("last_rebalance_at")
        if last_iso is not None:
            last_at = datetime.fromisoformat(last_iso)
            if (now - last_at).total_seconds() < interval:
                return []

        target_value = fractions[phase] * ctx.capital_allocated_usd
        count = int(ctx.state.get("rebalance_count", 0))
        units = Decimal(ctx.state.get("position_units", "0"))

        intents, new_units = rebalance_toward_value(
            ctx=ctx, target_value=target_value, mid=mid, units=units,
            coid_prefix=_COID_PREFIX, seq=count,
        )

        ctx.state["rebalance_count"] = count + 1
        ctx.state["position_units"] = str(new_units)
        ctx.state["last_rebalance_at"] = now.isoformat()
        return intents

    def graceful_shutdown(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def emergency_stop(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []

    def expected_return_for_regime(self, regime: Regime) -> ReturnExpectation:
        # Spec §6.2 compat: ["*"]. A calendar-driven cycle overlay.
        match regime:
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.3,
                    rationale="Heavy through the bull phase of the cycle",
                )
            case Regime.TREND_DOWN:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.008"),
                    confidence=0.3,
                    rationale="Light through bear; small drawdown by design",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.006"),
                    confidence=0.3,
                    rationale="Calendar overlay; modest in range regimes",
                )
