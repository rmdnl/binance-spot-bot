"""Add persistent inventory positions.

Revision ID: 20261016_0002
Revises: 20261016_0001
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0002"
down_revision = "20261016_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("base_asset", sa.String(length=20), nullable=False),
        sa.Column("quote_asset", sa.String(length=20), nullable=False),
        sa.Column("base_quantity", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("quote_cost", sa.Numeric(38, 18), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("environment", "symbol", name="uq_inventory_environment_symbol"),
    )
    op.create_index("ix_inventory_positions_environment", "inventory_positions", ["environment"])
    op.create_index("ix_inventory_positions_symbol", "inventory_positions", ["symbol"])


def downgrade() -> None:
    op.drop_table("inventory_positions")
