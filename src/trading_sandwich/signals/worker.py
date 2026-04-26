"""Signal worker. Celery consumer that reads features context, iterates the
detector registry, applies three-stage gating, persists results, and schedules
outcome measurement for claude_triaged signals.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_sandwich._async import run_coro
from trading_sandwich.celery_app import app
from trading_sandwich.contracts.models import FeaturesRow, Signal
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Features as FeaturesORM
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.logging import get_logger
from trading_sandwich.metrics import SIGNALS_FIRED
from trading_sandwich.signals.detectors import REGISTRY
from trading_sandwich.signals.gating import gate_signal_with_db

logger = get_logger(__name__)

LOOKBACK = 60
HORIZONS_SECONDS: dict[str, int] = {
    "15m": 15 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60,
    "24h": 24 * 60 * 60, "3d": 3 * 24 * 60 * 60, "7d": 7 * 24 * 60 * 60,
}


def _row_to_features(r: FeaturesORM) -> FeaturesRow:
    return FeaturesRow(
        symbol=r.symbol, timeframe=r.timeframe, close_time=r.close_time,
        close_price=r.close_price,
        ema_8=r.ema_8, ema_21=r.ema_21, ema_55=r.ema_55, ema_200=r.ema_200,
        rsi_14=r.rsi_14, atr_14=r.atr_14,
        macd_line=r.macd_line, macd_signal=r.macd_signal, macd_hist=r.macd_hist,
        adx_14=r.adx_14, di_plus_14=r.di_plus_14, di_minus_14=r.di_minus_14,
        stoch_rsi_k=r.stoch_rsi_k, stoch_rsi_d=r.stoch_rsi_d, roc_10=r.roc_10,
        bb_upper=r.bb_upper, bb_middle=r.bb_middle, bb_lower=r.bb_lower, bb_width=r.bb_width,
        keltner_upper=r.keltner_upper, keltner_middle=r.keltner_middle, keltner_lower=r.keltner_lower,
        donchian_upper=r.donchian_upper, donchian_middle=r.donchian_middle, donchian_lower=r.donchian_lower,
        obv=r.obv, vwap=r.vwap, volume_zscore_20=r.volume_zscore_20, mfi_14=r.mfi_14,
        swing_high_5=r.swing_high_5, swing_low_5=r.swing_low_5,
        pivot_p=r.pivot_p, pivot_r1=r.pivot_r1, pivot_r2=r.pivot_r2,
        pivot_s1=r.pivot_s1, pivot_s2=r.pivot_s2,
        prior_day_high=r.prior_day_high, prior_day_low=r.prior_day_low,
        prior_week_high=r.prior_week_high, prior_week_low=r.prior_week_low,
        funding_rate=r.funding_rate, funding_rate_24h_mean=r.funding_rate_24h_mean,
        open_interest_usd=r.open_interest_usd,
        oi_delta_1h=r.oi_delta_1h, oi_delta_24h=r.oi_delta_24h,
        long_short_ratio=r.long_short_ratio, ob_imbalance_05=r.ob_imbalance_05,
        ema_21_slope_bps=r.ema_21_slope_bps,
        atr_percentile_100=r.atr_percentile_100,
        bb_width_percentile_100=r.bb_width_percentile_100,
        trend_regime=r.trend_regime, vol_regime=r.vol_regime,
        feature_version=r.feature_version,
    )


async def _load_features(symbol: str, timeframe: str, close_time: datetime) -> list[FeaturesRow]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        orm_rows = (await session.execute(
            select(FeaturesORM)
            .where(
                FeaturesORM.symbol == symbol,
                FeaturesORM.timeframe == timeframe,
                FeaturesORM.close_time <= close_time,
            )
            .order_by(FeaturesORM.close_time.desc())
            .limit(LOOKBACK)
        )).scalars().all()
    return [_row_to_features(r) for r in reversed(orm_rows)]


async def _persist_signal(signal: Signal) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        stmt = pg_insert(SignalORM).values(
            signal_id=signal.signal_id, symbol=signal.symbol, timeframe=signal.timeframe,
            archetype=signal.archetype, fired_at=signal.fired_at,
            candle_close_time=signal.candle_close_time,
            trigger_price=signal.trigger_price, direction=signal.direction,
            confidence=signal.confidence, confidence_breakdown=signal.confidence_breakdown,
            gating_outcome=signal.gating_outcome,
            features_snapshot=signal.features_snapshot,
            stop_price=signal.stop_price, target_price=signal.target_price, rr_ratio=signal.rr_ratio,
            detector_version=signal.detector_version,
        ).on_conflict_do_nothing(index_elements=["signal_id"])
        await session.execute(stmt)
        await session.commit()


def _schedule_outcomes(signal: Signal) -> None:
    from trading_sandwich.outcomes.worker import measure_outcome as measure_outcome_task
    for horizon, secs in HORIZONS_SECONDS.items():
        measure_outcome_task.apply_async(
            args=[str(signal.signal_id), horizon],
            queue="outcomes",
            countdown=secs,
        )


async def _detect_async(symbol: str, timeframe: str, close_time_iso: str) -> None:
    close_time = datetime.fromisoformat(close_time_iso)
    features = await _load_features(symbol, timeframe, close_time)
    if not features:
        return

    for archetype, detector_fn in REGISTRY.items():
        try:
            sig = detector_fn(features)
        except Exception as exc:
            logger.exception("detector_error", archetype=archetype, err=str(exc))
            continue
        if sig is None:
            continue

        gated = gate_signal_with_db(sig)
        await _persist_signal(gated)
        SIGNALS_FIRED.labels(
            symbol=sig.symbol, timeframe=sig.timeframe,
            archetype=sig.archetype, gating_outcome=gated.gating_outcome,
        ).inc()
        if gated.gating_outcome == "claude_triaged":
            _schedule_outcomes(gated)
            # Phase 2.7 — signal-driven triage is deprecated. The heartbeat
            # trader is the sole trigger now. Signals still get gated and
            # persisted (for get_recent_signals), but no longer spawn
            # one-shot Claude triages. Re-enable only by reverting Spec A.
            # from trading_sandwich.triage.worker import triage_signal
            # triage_signal.delay(str(gated.signal_id))


@app.task(name="trading_sandwich.signals.worker.detect_signals")
def detect_signals(symbol: str, timeframe: str, close_time_iso: str) -> None:
    run_coro(_detect_async(symbol, timeframe, close_time_iso))
