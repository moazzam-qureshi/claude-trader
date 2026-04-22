from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trading_sandwich.contracts.models import Candle, FeaturesRow, Outcome, Signal


def test_candle_roundtrip():
    c = Candle(
        symbol="BTCUSDT", timeframe="1m",
        open_time=datetime(2026, 4, 21, tzinfo=timezone.utc),
        close_time=datetime(2026, 4, 21, 0, 1, tzinfo=timezone.utc),
        open=Decimal("50000"), high=Decimal("50100"),
        low=Decimal("49990"), close=Decimal("50050"),
        volume=Decimal("12.5"),
    )
    dump = c.model_dump_json()
    c2 = Candle.model_validate_json(dump)
    assert c2 == c


def test_features_row_requires_version():
    with pytest.raises(ValidationError):
        FeaturesRow(
            symbol="BTCUSDT", timeframe="1m",
            close_time=datetime.now(timezone.utc),
            close_price=Decimal("50000"),
        )


def test_signal_direction_enum():
    with pytest.raises(ValidationError):
        Signal(
            signal_id=uuid4(), symbol="BTCUSDT", timeframe="1m",
            archetype="trend_pullback",
            fired_at=datetime.now(timezone.utc),
            candle_close_time=datetime.now(timezone.utc),
            trigger_price=Decimal("50000"),
            direction="sideways",
            confidence=Decimal("0.8"),
            confidence_breakdown={"rule": 0.8},
            gating_outcome="claude_triaged",
            features_snapshot={},
            detector_version="abc",
        )


def test_outcome_horizon_enum():
    with pytest.raises(ValidationError):
        Outcome(
            signal_id=uuid4(), horizon="30m",
            measured_at=datetime.now(timezone.utc),
            close_price=Decimal("50000"), return_pct=Decimal("0.01"),
            mfe_pct=Decimal("0.02"), mae_pct=Decimal("-0.005"),
            stop_hit_1atr=False, target_hit_2atr=False,
        )
