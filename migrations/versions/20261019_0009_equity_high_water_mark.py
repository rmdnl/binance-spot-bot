"""Persist per-environment equity high-water marks.

Revision ID: 20261019_0009
Revises: 20261019_0008
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0009"
down_revision = "20261019_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_high_water_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column("current_equity", sa.Numeric(38, 18), nullable=False),
        sa.Column("peak_equity", sa.Numeric(38, 18), nullable=False),
        sa.Column("drawdown_pct", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("environment", name="uq_equity_high_water_environment"),
    )
    op.create_index("ix_equity_high_water_marks_environment", "equity_high_water_marks", ["environment"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_equity_high_water_marks_environment", table_name="equity_high_water_marks")
    op.drop_table("equity_high_water_marks")
