from datetime import UTC, datetime
from decimal import Decimal as D

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, InventoryPosition, RealizedPNLEntry
from spot_bot.pnl import pnl_snapshot, position_for_symbol


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_pnl_reads_persisted_inventory_and_realized_ledger():
    engine = make_db()
    with Session(engine) as db:
        db.add(InventoryPosition(
            environment=Environment.TESTNET,
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            base_quantity=D("0.25000000"),
            quote_cost=D("15000.00"),
        ))
        db.add(RealizedPNLEntry(
            fill_id=99,
            environment=Environment.TESTNET,
            symbol="BTC/USDT",
            quantity=D("0.10"),
            entry_cost=D("5000"),
            proceeds=D("5500"),
            fees=D("5"),
            realized_pnl=D("495"),
            timestamp=datetime.now(UTC),
        ))
        db.commit()

        qty, cost, realized = position_for_symbol(db, "BTC/USDT")
        assert qty == D("0.25000000")
        assert cost == D("15000.00")
        assert realized == D("495")

        snapshot = pnl_snapshot(db, "BTC/USDT", D("70000"))
        assert snapshot["realized_pnl_quote"] == "495"
        assert snapshot["unrealized_pnl_quote"] == "2500.00"


def test_missing_ledger_returns_zero_without_replaying_fills():
    engine = make_db()
    with Session(engine) as db:
        assert position_for_symbol(db, "ETH/USDT") == (D("0"), D("0"), D("0"))
