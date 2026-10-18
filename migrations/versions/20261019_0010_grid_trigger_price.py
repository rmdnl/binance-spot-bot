"""Persist Grid Bot downward trigger price.

Revision ID: 20261019_0010
Revises: 20261019_0009
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0010"
down_revision = "20261019_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("grid_bots", sa.Column("trigger_price", sa.Numeric(38, 18), nullable=True))


def downgrade() -> None:
    op.drop_column("grid_bots", "trigger_price")
