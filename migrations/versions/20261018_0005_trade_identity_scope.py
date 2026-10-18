"""Scope exchange trade identity to environment and symbol.

Revision ID: 20261018_0005
Revises: 20261016_0004
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0005"
down_revision = "20261016_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "spot_fills",
        sa.Column("environment", sa.String(length=10), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE spot_fills "
            "SET environment = ("
            "SELECT environment FROM spot_orders "
            "WHERE spot_orders.id = spot_fills.order_id"
            ")"
        )
    )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "spot_fills",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch_op:
            batch_op.drop_constraint(
                "uq_spot_fills_exchange_trade_id",
                type_="unique",
            )
            batch_op.alter_column(
                "environment",
                existing_type=sa.String(length=10),
                nullable=False,
            )
            batch_op.create_unique_constraint(
                "uq_spot_fill_environment_symbol_trade_id",
                ["environment", "symbol", "exchange_trade_id"],
            )
    else:
        unique_constraints = sa.inspect(bind).get_unique_constraints("spot_fills")
        old_constraint = next(
            (
                item["name"]
                for item in unique_constraints
                if item.get("column_names") == ["exchange_trade_id"]
            ),
            None,
        )
        if old_constraint is None:
            raise RuntimeError("could not find the existing exchange_trade_id unique constraint")
        op.alter_column(
            "spot_fills",
            "environment",
            existing_type=sa.String(length=10),
            nullable=False,
        )
        op.drop_constraint(old_constraint, "spot_fills", type_="unique")
        op.create_unique_constraint(
            "uq_spot_fill_environment_symbol_trade_id",
            "spot_fills",
            ["environment", "symbol", "exchange_trade_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "spot_fills",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch_op:
            batch_op.drop_constraint(
                "uq_spot_fill_environment_symbol_trade_id",
                type_="unique",
            )
            batch_op.drop_column("environment")
            batch_op.create_unique_constraint(
                "uq_spot_fills_exchange_trade_id",
                ["exchange_trade_id"],
            )
    else:
        op.drop_constraint(
            "uq_spot_fill_environment_symbol_trade_id",
            "spot_fills",
            type_="unique",
        )
        op.drop_column("spot_fills", "environment")
        op.create_unique_constraint(
            "uq_spot_fills_exchange_trade_id",
            "spot_fills",
            ["exchange_trade_id"],
        )
