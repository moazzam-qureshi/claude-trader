"""Read tools: get_signal, get_market_snapshot, find_similar_signals, get_archetype_stats."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from uuid import UUID

from sqlalchemy import exists, select

from trading_sandwich.contracts.phase2 import (
    ArchetypeStats,
    MarketSnapshot,
    SignalDetail,
    SimilarSignal,
    SimilarSignalsResult,
)
from trading_sandwich.db.engine import get_session_factory
from trading_sandwich.db.models import Features
from trading_sandwich.db.models import Signal as SignalORM
from trading_sandwich.db.models import SignalOutcome
from trading_sandwich.mcp.server import mcp


# ----- get_signal -----------------------------------------------------------


async def _load_signal_with_outcomes(signal_id: UUID) -> tuple[dict | None, list[dict]]:
    factory = get_session_factory()
    async with factory() as session:
        sig = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == signal_id)
        )).scalar_one_or_none()
        if sig is None:
            return None, []
        outs = (await session.execute(
            select(SignalOutcome).where(SignalOutcome.signal_id == signal_id)
        )).scalars().all()
        sig_dict = {
            "signal_id": sig.signal_id,
            "symbol": sig.symbol,
            "timeframe": sig.timeframe,
            "archetype": sig.archetype,
            "direction": sig.direction,
            "fired_at": sig.fired_at,
            "trigger_price": sig.trigger_price,
            "confidence": sig.confidence,
            "confidence_breakdown": sig.confidence_breakdown,
            "features_snapshot": sig.features_snapshot,
        }
        out_dicts = [
            {
                "horizon": o.horizon,
                "return_pct": float(o.return_pct),
                "mfe_in_atr": float(o.mfe_in_atr) if o.mfe_in_atr is not None else None,
                "mae_in_atr": float(o.mae_in_atr) if o.mae_in_atr is not None else None,
                "stop_hit_1atr": o.stop_hit_1atr,
                "target_hit_2atr": o.target_hit_2atr,
            }
            for o in outs
        ]
    return sig_dict, out_dicts


@mcp.tool()
async def get_signal(signal_id: UUID) -> SignalDetail:
    """Load one signal by id with its features snapshot and any measured outcomes."""
    row, outcomes = await _load_signal_with_outcomes(signal_id)
    if row is None:
        raise ValueError(f"signal {signal_id} not found")
    return SignalDetail(**row, outcomes_so_far=outcomes)


# ----- get_market_snapshot --------------------------------------------------


def _policy_timeframes() -> list[str]:
    from trading_sandwich._policy import load_policy
    return list(load_policy()["timeframes"])


_SNAPSHOT_COLS = [
    "close_price", "trend_regime", "vol_regime",
    "ema_8", "ema_21", "ema_55", "ema_200",
    "adx_14", "atr_14", "atr_percentile_100", "bb_width_percentile_100",
    "funding_rate", "open_interest_usd",
    "prior_day_high", "prior_day_low",
    "prior_week_high", "prior_week_low",
    "pivot_p",
]


async def _load_latest_features(symbol: str, timeframe: str) -> dict | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(Features)
            .where(Features.symbol == symbol, Features.timeframe == timeframe)
            .order_by(Features.close_time.desc())
            .limit(1)
        )).scalar_one_or_none()
        if row is None:
            return None
        out: dict = {}
        for col in _SNAPSHOT_COLS:
            v = getattr(row, col, None)
            if v is None:
                continue
            out[col] = float(v) if isinstance(v, Decimal) else v
        return out


@mcp.tool()
async def get_market_snapshot(symbol: str) -> MarketSnapshot:
    """For each timeframe in the universe, returns the most recent feature row."""
    per_tf: dict[str, dict | None] = {}
    for tf in _policy_timeframes():
        per_tf[tf] = await _load_latest_features(symbol, tf)
    return MarketSnapshot(symbol=symbol, per_timeframe=per_tf)


# ----- find_similar_signals -------------------------------------------------


def _confidence_bucket(conf: Decimal) -> str:
    if conf <= Decimal("0.33"):
        return "low"
    if conf <= Decimal("0.66"):
        return "mid"
    return "high"


def _bucket_bounds(bucket: str) -> tuple[Decimal, Decimal]:
    return {
        "low": (Decimal("0"), Decimal("0.33")),
        "mid": (Decimal("0.3300000001"), Decimal("0.66")),
        "high": (Decimal("0.6600000001"), Decimal("1")),
    }[bucket]


@mcp.tool()
async def find_similar_signals(signal_id: UUID, k: int = 20) -> SimilarSignalsResult:
    """Structural similarity: same (archetype, direction, trend_regime, vol_regime,
    confidence_bucket). Only returns signals with at least one measured outcome.
    """
    factory = get_session_factory()
    async with factory() as session:
        seed = (await session.execute(
            select(SignalORM).where(SignalORM.signal_id == signal_id)
        )).scalar_one_or_none()
        if seed is None:
            raise ValueError(f"signal {signal_id} not found")

        trend = seed.features_snapshot.get("trend_regime")
        vol = seed.features_snapshot.get("vol_regime")
        bucket = _confidence_bucket(seed.confidence)
        lo, hi = _bucket_bounds(bucket)

        stmt = (
            select(SignalORM)
            .where(
                SignalORM.archetype == seed.archetype,
                SignalORM.direction == seed.direction,
                SignalORM.gating_outcome == "claude_triaged",
                SignalORM.confidence >= lo,
                SignalORM.confidence <= hi,
                SignalORM.signal_id != signal_id,
                exists().where(SignalOutcome.signal_id == SignalORM.signal_id),
            )
            .order_by(SignalORM.fired_at.desc())
            .limit(k)
        )
        # Note: (trend_regime, vol_regime) live in features_snapshot JSONB —
        # apply a Python post-filter for portability.
        candidates = (await session.execute(stmt)).scalars().all()
        filtered = [
            c for c in candidates
            if c.features_snapshot.get("trend_regime") == trend
            and c.features_snapshot.get("vol_regime") == vol
        ]

        results: list[SimilarSignal] = []
        for c in filtered:
            outs = (await session.execute(
                select(SignalOutcome).where(SignalOutcome.signal_id == c.signal_id)
            )).scalars().all()
            results.append(SimilarSignal(
                signal_id=c.signal_id,
                fired_at=c.fired_at,
                archetype=c.archetype,
                direction=c.direction,
                trend_regime=trend,
                vol_regime=vol,
                confidence=c.confidence,
                outcomes=[
                    {
                        "horizon": o.horizon,
                        "return_pct": float(o.return_pct),
                        "mfe_in_atr": float(o.mfe_in_atr) if o.mfe_in_atr is not None else None,
                        "mae_in_atr": float(o.mae_in_atr) if o.mae_in_atr is not None else None,
                        "stop_hit_1atr": o.stop_hit_1atr,
                        "target_hit_2atr": o.target_hit_2atr,
                    }
                    for o in outs
                ],
            ))
    return SimilarSignalsResult(results=results, sparse=len(results) < k)


# ----- get_archetype_stats --------------------------------------------------


@mcp.tool()
async def get_archetype_stats(archetype: str, lookback_days: int = 30) -> ArchetypeStats:
    """Aggregate per-archetype stats over the lookback window."""
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    factory = get_session_factory()
    async with factory() as session:
        sigs = (await session.execute(
            select(SignalORM).where(
                SignalORM.archetype == archetype,
                SignalORM.fired_at >= since,
                SignalORM.gating_outcome == "claude_triaged",
            )
        )).scalars().all()
        by_bucket: dict[tuple, dict] = {}
        for s in sigs:
            key = (s.direction, s.features_snapshot.get("trend_regime"),
                   s.features_snapshot.get("vol_regime"))
            slot = by_bucket.setdefault(key, {
                "direction": key[0],
                "trend_regime": key[1],
                "vol_regime": key[2],
                "count": 0,
                "returns_24h": [],
                "target_hits": 0,
                "stop_hits": 0,
            })
            slot["count"] += 1
            outs = (await session.execute(
                select(SignalOutcome).where(
                    SignalOutcome.signal_id == s.signal_id,
                    SignalOutcome.horizon == "24h",
                )
            )).scalars().all()
            for o in outs:
                slot["returns_24h"].append(float(o.return_pct))
                if o.target_hit_2atr:
                    slot["target_hits"] += 1
                if o.stop_hit_1atr:
                    slot["stop_hits"] += 1

        buckets_out = []
        for slot in by_bucket.values():
            rets = slot.pop("returns_24h")
            slot["median_return_24h"] = median(rets) if rets else None
            slot["win_rate_24h"] = (
                sum(1 for r in rets if r > 0) / len(rets) if rets else None
            )
            slot["target_hit_rate"] = slot.pop("target_hits") / slot["count"]
            slot["stop_hit_rate"] = slot.pop("stop_hits") / slot["count"]
            buckets_out.append(slot)

    return ArchetypeStats(
        archetype=archetype,
        lookback_days=lookback_days,
        total_fires=sum(b["count"] for b in buckets_out),
        by_bucket=buckets_out,
    )
