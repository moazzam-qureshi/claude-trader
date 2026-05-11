"""orders.direction column — trade direction (buy vs sell)

Revision ID: 0018
Revises: 0017

The execution rail was built for the Phase 2.7 single-trade flow where
every order is a long entry — `orders.side` ('long'/'short') sufficed.
The mechanical-strategy path (Phase 3 path-to-production) emits sells
too: a grid sells its inventory at a higher rung, a rebalance trims a
position. Those are still long-only spot orders (you can only sell what
you hold), so `side` stays 'long' — but the *trade* direction matters
to the adapter (a CCXT `create_order(side='sell')`) and to the paper
matcher (a sell-limit fills when price rises to it, not falls).

`direction` is 'buy' for every existing row and every proposal-path
order (default), 'sell' only for strategy sell-against-fill / rebalance
orders. Halal-spot is unaffected: a 'sell' only ever reduces an
existing long, never opens a short.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "direction", sa.Text(), nullable=False, server_default="buy",
        ),
    )
    op.create_check_constraint(
        "ck_orders_direction_valid", "orders", "direction IN ('buy', 'sell')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_orders_direction_valid", "orders", type_="check")
    op.drop_column("orders", "direction")
