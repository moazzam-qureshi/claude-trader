"""E3 BTC Dominance Rotation — Phase 3 Wave 1 Task 2.23.

Per-symbol rotation by BTC.D direction: when BTC dominance is rising,
hold BTC heavy and alts light; when falling, the reverse. Deployed on
a single symbol, this strategy carries an asset_class tag ("btc" or
"alt") and sizes its position large or small depending on whether
BTC.D currently favours that class.

  favourable = (asset_class == "btc" and btc_dominance_rising) or
               (asset_class == "alt" and not btc_dominance_rising)
  target_value = (high_fraction if favourable else low_fraction) * capital
  then close the gap to actual position value (rebalance/_base.py)

Slow cadence: first tick acts immediately, subsequent only after
interval_seconds; no catch-up after worker downtime.

Halal-spot inviolable: side='long' on every intent. The trim-down's
sell value caps at the held value — never goes short. Position units
estimated as size_usd / mid on a buy; fill-delivery plumbing corrects
later. Reuses rebalance/_base.py's rebalance_toward_value().

Snapshot contract: {'now': datetime (tz-aware), 'mid_price': Decimal,
'btc_dominance_rising': bool}. The supporting task derives the
direction from the TradingView BTC.D feed. State: position_units,
rebalance_count, last_rebalance_at (iso).

Spec §6.2 compat: ["*"] — always on, slow.
"""
from __future__ import annotations

from datetime import datetime
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


_COID_PREFIX = "rotbtc"
_VALID_CLASSES = {"btc", "alt"}


def _read_params(params: dict[str, Any]) -> tuple[str, Decimal, Decimal, int]:
    try:
        asset_class = str(params["asset_class"])
        high_fraction = Decimal(str(params["high_fraction"]))
        low_fraction = Decimal(str(params["low_fraction"]))
        interval = int(params["interval_seconds"])
    except KeyError as e:
        raise KeyError(
            f"rotation_btc_dominance params missing required key: {e}"
        ) from e
    if asset_class not in _VALID_CLASSES:
        raise ValueError(
            f"asset_class must be one of {sorted(_VALID_CLASSES)}, "
            f"got {asset_class!r}"
        )
    for name, frac in (("high_fraction", high_fraction),
                       ("low_fraction", low_fraction)):
        if frac <= Decimal("0") or frac > Decimal("1"):
            raise ValueError(f"{name} must be in (0, 1], got {frac}")
    if low_fraction > high_fraction:
        raise ValueError(
            f"low_fraction ({low_fraction}) must be <= high_fraction "
            f"({high_fraction})"
        )
    if interval <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval}")
    return asset_class, high_fraction, low_fraction, interval


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("snapshot['now'] must be timezone-aware")
    return now


class BtcDominanceRotationStrategy(Strategy):
    """E3 BTC Dominance Rotation — heavy/light by BTC.D direction."""

    def tick(
        self, ctx: StrategyContext, snapshot: dict
    ) -> list[OrderIntent]:
        for k in ("now", "mid_price", "btc_dominance_rising"):
            if k not in snapshot:
                raise KeyError(
                    f"rotation_btc_dominance requires snapshot[{k!r}]"
                )
        now = _require_aware(snapshot["now"])
        mid = Decimal(str(snapshot["mid_price"]))
        rising = bool(snapshot["btc_dominance_rising"])
        asset_class, high_fraction, low_fraction, interval = _read_params(
            ctx.params
        )

        last_iso = ctx.state.get("last_rebalance_at")
        if last_iso is not None:
            last_at = datetime.fromisoformat(last_iso)
            if (now - last_at).total_seconds() < interval:
                return []

        favourable = (
            (asset_class == "btc" and rising)
            or (asset_class == "alt" and not rising)
        )
        fraction = high_fraction if favourable else low_fraction
        target_value = fraction * ctx.capital_allocated_usd

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
        # Spec §6.2 compat: ["*"]. A slow always-on overlay.
        match regime:
            case Regime.TREND_UP:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.012"),
                    confidence=0.35,
                    rationale="Rides whichever side BTC.D favours in the run",
                )
            case Regime.RANGE_VOLATILE:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.008"),
                    confidence=0.3,
                    rationale="Catches BTC.D swings between BTC and alts",
                )
            case _:
                return ReturnExpectation(
                    monthly_return_pct=Decimal("0.005"),
                    confidence=0.3,
                    rationale="Slow rotation overlay; modest in calm/down",
                )
