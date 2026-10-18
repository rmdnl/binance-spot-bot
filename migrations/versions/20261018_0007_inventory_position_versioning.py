"""Add optimistic concurrency version to inventory positions.

Revision ID: 20261018_0007
Revises: 20261018_0006
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0007"
down_revision = "20261018_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the server default for existing rows and direct SQL inserts.
    op.add_column(
        "inventory_positions",
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("inventory_positions", "version_id")
