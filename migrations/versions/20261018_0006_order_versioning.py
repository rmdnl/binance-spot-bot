"""Add optimistic concurrency version to spot orders.

Revision ID: 20261018_0006
Revises: 20261018_0005
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0006"
down_revision = "20261018_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spot_orders",
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )
    # The server default is only needed to populate existing rows during
    # migration. SQLAlchemy supplies version 1 for new ORM-created rows.
    op.alter_column(
        "spot_orders",
        "version_id",
        server_default=None,
        existing_type=sa.Integer(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_column("spot_orders", "version_id")
