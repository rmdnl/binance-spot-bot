"""Create the initial Spot Bot schema.

Revision ID: 20261016_0001
Revises:
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spot_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18)),
        sa.Column("filled_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("average_price", sa.Numeric(38, 18)),
        sa.Column("fee_quote", sa.Numeric(38, 18), nullable=False),
        sa.Column("client_order_id", sa.String(length=36), unique=True),
        sa.Column("exchange_order_id", sa.String(length=64), unique=True),
        sa.Column("error_message", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_spot_orders_environment", "spot_orders", ["environment"])
    op.create_index("ix_spot_orders_symbol", "spot_orders", ["symbol"])
    op.create_index("ix_spot_orders_status", "spot_orders", ["status"])

    op.create_table(
        "spot_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("spot_orders.id"), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee_currency", sa.String(length=20), nullable=False),
        sa.Column("exchange_trade_id", sa.String(length=64), unique=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_spot_fills_order_id", "spot_fills", ["order_id"])
    op.create_index("ix_spot_fills_symbol", "spot_fills", ["symbol"])

    op.create_table(
        "grid_bots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("grid_type", sa.String(length=12), nullable=False),
        sa.Column("lower_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("upper_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("grids", sa.Integer(), nullable=False),
        sa.Column("capital_quote", sa.Numeric(38, 18), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("max_open_orders", sa.Integer(), nullable=False),
        sa.Column("stop_loss_price", sa.Numeric(38, 18)),
        sa.Column("stop_loss_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_grid_bots_environment", "grid_bots", ["environment"])
    op.create_index("ix_grid_bots_symbol", "grid_bots", ["symbol"])
    op.create_index("ix_grid_bots_status", "grid_bots", ["status"])

    op.create_table(
        "grid_levels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bot_id", sa.Integer(), sa.ForeignKey("grid_bots.id"), nullable=False),
        sa.Column("level_index", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("buy_order_id", sa.Integer(), sa.ForeignKey("spot_orders.id")),
        sa.Column("sell_order_id", sa.Integer(), sa.ForeignKey("spot_orders.id")),
        sa.UniqueConstraint("bot_id", "level_index", name="uq_grid_level"),
    )
    op.create_index("ix_grid_levels_bot_id", "grid_levels", ["bot_id"])

    op.create_table(
        "risk_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("halted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=500)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("grid_levels")
    op.drop_table("grid_bots")
    op.drop_table("spot_fills")
    op.drop_table("spot_orders")
    op.drop_table("risk_state")
