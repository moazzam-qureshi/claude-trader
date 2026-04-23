from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trading_sandwich.contracts.models import Signal
from trading_sandwich.signals.gating import GatingState, apply_gating


def _mk_signal(confidence: float, fired_at: datetime, symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        signal_id=uuid4(), symbol=symbol, timeframe="1m",
        archetype="trend_pullback",
        fired_at=fired_at,
        candle_close_time=fired_at,
        trigger_price=Decimal("100"),
        direction="long",
        confidence=Decimal(str(confidence)),
        confidence_breakdown={},
        gating_outcome="below_threshold",
        features_snapshot={},
        detector_version="test",
    )


def test_below_threshold_suppressed():
    state = GatingState()
    policy = {"per_archetype_confidence_threshold": {"trend_pullback": 0.7},
              "per_archetype_cooldown_minutes": {"trend_pullback": 15}}
    s = _mk_signal(0.5, datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
    out = apply_gating(s, state, policy)
    assert out.gating_outcome == "below_threshold"


def test_above_threshold_triaged():
    state = GatingState()
    policy = {"per_archetype_confidence_threshold": {"trend_pullback": 0.7},
              "per_archetype_cooldown_minutes": {"trend_pullback": 15}}
    s = _mk_signal(0.9, datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
    out = apply_gating(s, state, policy)
    assert out.gating_outcome == "claude_triaged"


def test_cooldown_suppresses_second():
    state = GatingState()
    policy = {"per_archetype_confidence_threshold": {"trend_pullback": 0.7},
              "per_archetype_cooldown_minutes": {"trend_pullback": 15}}
    base = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    first = apply_gating(_mk_signal(0.9, base), state, policy)
    assert first.gating_outcome == "claude_triaged"
    second = apply_gating(_mk_signal(0.9, base + timedelta(minutes=5)), state, policy)
    assert second.gating_outcome == "cooldown_suppressed"
    third = apply_gating(_mk_signal(0.9, base + timedelta(minutes=20)), state, policy)
    assert third.gating_outcome == "claude_triaged"
