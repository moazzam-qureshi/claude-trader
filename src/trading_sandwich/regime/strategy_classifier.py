"""Phase 3 strategy regime classifier — plan Task 1.8.

Maps multi-signal market state (ADX, ATR%, MA structure) to a single
Regime enum (defined in strategies.base). This is the regime that
strategies are matched against in the strategy↔regime compatibility
config (Task 1.9).

Two layers:

  1. classify_signals(signals, thresholds) -> Regime
     Pure function. Deterministic. Unit-testable.

  2. classify_and_log(symbol, timeframe, signals) -> Regime
     Loads thresholds from settings.repo, calls classify_signals,
     persists the classification to regime_classifications, applies
     2-consecutive hysteresis, persists a regime_pivots row when the
     hysteresis clears a regime change.

The medium-term MA in spec §3.3 is `ma50`; the existing feature stack
computes ema_8/21/55/200 (no SMA-50 or EMA-50). We use ema_55 as the
medium-term MA. The semantic intent — "is price above the
medium-term MA, is the medium-term above the long-term, is the slope
positive" — survives the off-by-5.

Hysteresis logic (spec §3.3 + §6.2 hysteresis_required_consecutive=2):

  raw_now = classify_signals(...)            # current cycle's raw call
  prior_raw = last classification on (symbol, timeframe)  (or None)
  effective_now = (
      raw_now if (raw_now == prior_raw) else  # 2-consecutive cleared
      effective_prior                          # otherwise hold prior
  )

  pivot_fires_iff effective_now != effective_prior
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings
from trading_sandwich.strategies.base import Regime


@dataclass(frozen=True)
class RegimeSignals:
    """Inputs to the classifier. Caller (worker / MCP tool) extracts
    these from the latest features row + recent prior rows."""

    price: Decimal
    ma_medium: Decimal | None       # ema_55 — proxy for spec's ma50
    ma_long: Decimal | None         # ema_200
    ma_medium_slope_bps: float | None  # slope of ma_medium in bps
    adx: float | None               # adx_14
    atr_pct: float | None           # atr_14 / price


DEFAULT_THRESHOLDS: dict[str, Any] = {
    "adx_trend_threshold": 25,
    "adx_range_threshold": 20,
    "atr_pct_volatile_threshold": 0.03,
    "atr_pct_quiet_threshold": 0.015,
    "hysteresis_required_consecutive": 2,
}


def classify_signals(sig: RegimeSignals, thresholds: dict[str, Any]) -> Regime:
    """Pure rule-based regime classifier — spec §3.3.

    Returns TRANSITIONING when:
      - any required input is missing (warmup), OR
      - signals fall in a middle band (neither clearly trending nor
        clearly ranging).
    """
    if (
        sig.ma_medium is None
        or sig.ma_long is None
        or sig.ma_medium_slope_bps is None
        or sig.adx is None
        or sig.atr_pct is None
    ):
        return Regime.TRANSITIONING

    adx_trend = float(thresholds["adx_trend_threshold"])
    adx_range = float(thresholds["adx_range_threshold"])
    atr_volatile = float(thresholds["atr_pct_volatile_threshold"])
    atr_quiet = float(thresholds["atr_pct_quiet_threshold"])

    price = sig.price
    ma_med = sig.ma_medium
    ma_long = sig.ma_long

    # Trend regimes: strong directional flow (ADX above trend threshold)
    # AND consistent MA structure AND slope sign matches.
    if sig.adx > adx_trend:
        if price > ma_med > ma_long and sig.ma_medium_slope_bps > 0:
            return Regime.TREND_UP
        if price < ma_med < ma_long and sig.ma_medium_slope_bps < 0:
            return Regime.TREND_DOWN
        # Strong ADX but MAs misaligned → not a clean trend
        return Regime.TRANSITIONING

    # Range regimes: ADX below range threshold; sub-classify by ATR%.
    if sig.adx < adx_range:
        if sig.atr_pct > atr_volatile:
            return Regime.RANGE_VOLATILE
        if sig.atr_pct < atr_quiet:
            return Regime.RANGE_QUIET
        # Low ADX but ATR% in middle band → unclassifiable range
        return Regime.TRANSITIONING

    # ADX between range and trend thresholds → transitioning
    return Regime.TRANSITIONING


# --- DB-backed wrapper with hysteresis -----------------------------------


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def _effective_regime_from_history(
    conn, *, symbol: str, timeframe: str, required: int
) -> Regime | None:
    """The effective regime for (symbol, timeframe) given the
    classifications stream, with hysteresis applied retroactively.

    Walk classifications most-recent-first; the effective regime is the
    most recent regime that appears `required` times consecutively
    (i.e. it cleared its own hysteresis at the time it landed).

    Returns None when no regime has ever cleared hysteresis on this
    (symbol, timeframe) — the cold-start case.
    """
    r = await conn.execute(
        text(
            "SELECT regime FROM regime_classifications "
            "WHERE symbol = :s AND timeframe = :t "
            "ORDER BY id DESC LIMIT 200"
        ),
        {"s": symbol, "t": timeframe},
    )
    seq = [Regime(row[0]) for row in r]
    # seq is most-recent-first. Scan for a run of `required` of the same
    # value: the run's regime is the effective one.
    for i in range(len(seq) - required + 1):
        window = seq[i : i + required]
        if all(w == window[0] for w in window):
            return window[0]
    return None


async def classify_and_log(
    symbol: str,
    timeframe: str,
    signals: RegimeSignals,
    *,
    thresholds: dict[str, Any] | None = None,
) -> Regime:
    """Classify, log to regime_classifications, apply hysteresis, and
    write a regime_pivots row if the hysteresis clears a regime change.

    Returns the EFFECTIVE regime (what strategies should act on), which
    may lag the raw classification by one cycle if hysteresis hasn't
    cleared the candidate change.
    """
    thr = thresholds or DEFAULT_THRESHOLDS
    required = int(thr.get("hysteresis_required_consecutive", 2))
    raw_now = classify_signals(signals, thr)

    engine = _engine()
    try:
        async with engine.begin() as conn:
            # Compute effective regime BEFORE this classification lands.
            effective_prior = await _effective_regime_from_history(
                conn, symbol=symbol, timeframe=timeframe, required=required,
            )

            # Persist this classification.
            await conn.execute(
                text(
                    "INSERT INTO regime_classifications "
                    "(symbol, timeframe, regime, signals, classified_at) "
                    "VALUES (:s, :t, :r, CAST(:sig AS jsonb), NOW())"
                ),
                {
                    "s": symbol, "t": timeframe, "r": raw_now.value,
                    "sig": _signals_json(signals),
                },
            )

            # Recompute effective regime INCLUDING this classification.
            effective_now = await _effective_regime_from_history(
                conn, symbol=symbol, timeframe=timeframe, required=required,
            )

            # Pivot fires only when the effective regime actually
            # transitions between two known states. Cold-start
            # (effective_prior is None) doesn't write a pivot row —
            # that's just baseline establishment, not a transition.
            if (
                effective_prior is not None
                and effective_now is not None
                and effective_prior != effective_now
            ):
                await conn.execute(
                    text(
                        "INSERT INTO regime_pivots "
                        "(symbol, from_regime, to_regime, triggered_by, "
                        " triggered_at, actions_taken, prompt_version) "
                        "VALUES (:s, :fr, :to, 'classifier_hysteresis', NOW(), "
                        " CAST('{}' AS jsonb), NULL)"
                    ),
                    {
                        "s": symbol,
                        "fr": effective_prior.value,
                        "to": effective_now.value,
                    },
                )

            # The wrapper returns the effective regime — what strategies
            # should act on. If effective is still unset (no run cleared
            # hysteresis yet), fall through to the raw classification so
            # callers always get something usable.
            return effective_now if effective_now is not None else raw_now
    finally:
        await engine.dispose()


def _signals_json(sig: RegimeSignals) -> str:
    """Stable JSON for the signals JSONB column."""
    import json

    def _f(v):
        if v is None:
            return None
        if isinstance(v, Decimal):
            return float(v)
        return v

    return json.dumps({
        "price": _f(sig.price),
        "ma_medium": _f(sig.ma_medium),
        "ma_long": _f(sig.ma_long),
        "ma_medium_slope_bps": sig.ma_medium_slope_bps,
        "adx": sig.adx,
        "atr_pct": sig.atr_pct,
    })
