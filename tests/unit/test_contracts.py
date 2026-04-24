from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trading_sandwich.contracts.models import Candle, FeaturesRow, Outcome, Signal


def test_candle_roundtrip():
    c = Candle(
        symbol="BTCUSDT", timeframe="1m",
        open_time=datetime(2026, 4, 21, tzinfo=UTC),
        close_time=datetime(2026, 4, 21, 0, 1, tzinfo=UTC),
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
            close_time=datetime.now(UTC),
            close_price=Decimal("50000"),
        )


def test_signal_direction_enum():
    with pytest.raises(ValidationError):
        Signal(
            signal_id=uuid4(), symbol="BTCUSDT", timeframe="1m",
            archetype="trend_pullback",
            fired_at=datetime.now(UTC),
            candle_close_time=datetime.now(UTC),
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
            measured_at=datetime.now(UTC),
            close_price=Decimal("50000"), return_pct=Decimal("0.01"),
            mfe_pct=Decimal("0.02"), mae_pct=Decimal("-0.005"),
            stop_hit_1atr=False, target_hit_2atr=False,
        )


_PHASE_1_ARCHETYPES = [
    "trend_pullback", "squeeze_breakout",
    "divergence_rsi", "divergence_macd",
    "range_rejection",
    "liquidity_sweep_daily", "liquidity_sweep_swing",
    "funding_extreme",
]


def test_all_phase_1_archetypes_accepted():
    for arch in _PHASE_1_ARCHETYPES:
        s = Signal(
            signal_id=uuid4(), symbol="BTCUSDT", timeframe="5m",
            archetype=arch,
            fired_at=datetime.now(UTC),
            candle_close_time=datetime.now(UTC),
            trigger_price=Decimal("100"), direction="long",
            confidence=Decimal("0.7"),
            confidence_breakdown={},
            gating_outcome="below_threshold",
            features_snapshot={},
            detector_version="test",
        )
        assert s.archetype == arch


_PHASE_1_FEATURES_COLUMNS = [
    "ema_8", "ema_21", "ema_55", "ema_200",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
    "rsi_14", "atr_14",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "keltner_upper", "keltner_middle", "keltner_lower",
    "donchian_upper", "donchian_middle", "donchian_lower",
    "obv", "vwap", "volume_zscore_20", "mfi_14",
    "swing_high_5", "swing_low_5",
    "pivot_p", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2",
    "prior_day_high", "prior_day_low", "prior_week_high", "prior_week_low",
    "funding_rate", "funding_rate_24h_mean",
    "open_interest_usd", "oi_delta_1h", "oi_delta_24h",
    "long_short_ratio", "ob_imbalance_05",
    "ema_21_slope_bps", "atr_percentile_100", "bb_width_percentile_100",
]


def test_features_row_accepts_all_phase_1_columns():
    kwargs = {
        "symbol": "BTCUSDT", "timeframe": "5m",
        "close_time": datetime.now(UTC),
        "close_price": Decimal("100"),
        "feature_version": "test",
    }
    for col in _PHASE_1_FEATURES_COLUMNS:
        kwargs[col] = Decimal("1.23")
    row = FeaturesRow(**kwargs)
    for col in _PHASE_1_FEATURES_COLUMNS:
        assert getattr(row, col) == Decimal("1.23")


def test_features_row_all_new_columns_optional():
    row = FeaturesRow(
        symbol="BTCUSDT", timeframe="5m",
        close_time=datetime.now(UTC),
        close_price=Decimal("100"),
        feature_version="test",
    )
    for col in _PHASE_1_FEATURES_COLUMNS:
        # ema_21, rsi_14, atr_14 existed in Phase 0 as Optional[Decimal]; they
        # should also default to None when not provided.
        assert getattr(row, col) is None, f"{col} should default to None"


def test_unknown_archetype_rejected():
    with pytest.raises(ValidationError):
        Signal(
            signal_id=uuid4(), symbol="BTCUSDT", timeframe="5m",
            archetype="nonexistent_archetype",
            fired_at=datetime.now(UTC),
            candle_close_time=datetime.now(UTC),
            trigger_price=Decimal("100"), direction="long",
            confidence=Decimal("0.7"),
            confidence_breakdown={},
            gating_outcome="below_threshold",
            features_snapshot={},
            detector_version="test",
        )
