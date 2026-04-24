"""archetype_check

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


_ARCHETYPES = [
    "trend_pullback",
    "squeeze_breakout",
    "divergence_rsi",
    "divergence_macd",
    "range_rejection",
    "liquidity_sweep_daily",
    "liquidity_sweep_swing",
    "funding_extreme",
]


def upgrade() -> None:
    values = ", ".join(f"'{a}'" for a in _ARCHETYPES)
    op.create_check_constraint(
        "ck_signals_archetype_valid",
        "signals",
        f"archetype IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_signals_archetype_valid", "signals", type_="check")
