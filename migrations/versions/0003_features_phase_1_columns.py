"""features_phase_1_columns

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


_NEW_COLUMNS = [
    "ema_8", "ema_55", "ema_200",
    "macd_line", "macd_signal", "macd_hist",
    "adx_14", "di_plus_14", "di_minus_14",
    "stoch_rsi_k", "stoch_rsi_d", "roc_10",
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


def upgrade() -> None:
    for col in _NEW_COLUMNS:
        op.add_column("features", sa.Column(col, sa.Numeric, nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLUMNS):
        op.drop_column("features", col)
