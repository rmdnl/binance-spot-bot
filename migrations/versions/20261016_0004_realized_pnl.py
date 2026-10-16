"""Add durable realized PNL ledger.

Revision ID: 20261016_0004
Revises: 20261016_0003
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0004"
down_revision = "20261016_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realized_pnl_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fill_id", sa.Integer(), sa.ForeignKey("spot_fills.id"), nullable=False, unique=True),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("entry_cost", sa.Numeric(38, 18), nullable=False),
        sa.Column("proceeds", sa.Numeric(38, 18), nullable=False),
        sa.Column("fees", sa.Numeric(38, 18), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(38, 18), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_realized_pnl_entries_environment", "realized_pnl_entries", ["environment"])
    op.create_index("ix_realized_pnl_entries_symbol", "realized_pnl_entries", ["symbol"])
    op.create_index("ix_realized_pnl_entries_timestamp", "realized_pnl_entries", ["timestamp"])


def downgrade() -> None:
    op.drop_table("realized_pnl_entries")
