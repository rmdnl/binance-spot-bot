"""Add idempotency records for inventory fill application.

Revision ID: 20261016_0003
Revises: 20261016_0002
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0003"
down_revision = "20261016_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fill_id", sa.Integer(), sa.ForeignKey("spot_fills.id"), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fill_id", name="uq_inventory_application_fill"),
    )
    op.create_index("ix_inventory_applications_fill_id", "inventory_applications", ["fill_id"])


def downgrade() -> None:
    op.drop_table("inventory_applications")
