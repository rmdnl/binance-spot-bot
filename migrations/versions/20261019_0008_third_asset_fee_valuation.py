"""Persist quote-equivalent valuation for third-asset trade fees.

Revision ID: 20261019_0008
Revises: 20261018_0007
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0008"
down_revision = "20261018_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spot_fills",
        sa.Column("fee_quote_value", sa.Numeric(38, 18), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("spot_fills", "fee_quote_value")
