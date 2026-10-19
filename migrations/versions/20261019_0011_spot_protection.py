"""Persist manual Spot stop-loss and take-profit protection.

Revision ID: 20261019_0011
Revises: 20261019_0010
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0011"
down_revision = "20261019_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spot_protections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_order_id", sa.Integer(), sa.ForeignKey("spot_orders.id"), nullable=False),
        sa.Column("exit_order_id", sa.Integer(), sa.ForeignKey("spot_orders.id"), nullable=True),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("stop_loss_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("take_profit_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="waiting_entry"),
        sa.Column("trigger_reason", sa.String(20), nullable=True),
        sa.Column("protected_quantity", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("last_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_order_id", name="uq_spot_protection_entry_order"),
        sa.UniqueConstraint("exit_order_id", name="uq_spot_protection_exit_order"),
    )
    op.create_index("ix_spot_protections_entry_order_id", "spot_protections", ["entry_order_id"])
    op.create_index("ix_spot_protections_environment", "spot_protections", ["environment"])
    op.create_index("ix_spot_protections_symbol", "spot_protections", ["symbol"])
    op.create_index("ix_spot_protections_status", "spot_protections", ["status"])


def downgrade() -> None:
    op.drop_index("ix_spot_protections_status", table_name="spot_protections")
    op.drop_index("ix_spot_protections_symbol", table_name="spot_protections")
    op.drop_index("ix_spot_protections_environment", table_name="spot_protections")
    op.drop_index("ix_spot_protections_entry_order_id", table_name="spot_protections")
    op.drop_table("spot_protections")
