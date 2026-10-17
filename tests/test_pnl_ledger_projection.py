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


def add_position(db, environment, symbol, quantity, cost):
    db.add(InventoryPosition(
        environment=environment,
        symbol=symbol,
        base_asset=symbol.split("/")[0],
        quote_asset=symbol.split("/")[1],
        base_quantity=quantity,
        quote_cost=cost,
    ))


def add_realized(db, fill_id, environment, symbol, realized):
    db.add(RealizedPNLEntry(
        fill_id=fill_id,
        environment=environment,
        symbol=symbol,
        quantity=D("0.10"),
        entry_cost=D("5000"),
        proceeds=D("5500"),
        fees=D("5"),
        realized_pnl=realized,
        timestamp=datetime.now(UTC),
    ))


def test_pnl_reads_persisted_inventory_and_realized_ledger():
    engine = make_db()
    with Session(engine) as db:
        add_position(db, Environment.TESTNET, "BTC/USDT", D("0.25000000"), D("15000.00"))
        add_realized(db, 99, Environment.TESTNET, "BTC/USDT", D("495"))
        db.commit()

        qty, cost, realized = position_for_symbol(db, "BTC/USDT", Environment.TESTNET)
        assert qty == D("0.25000000")
        assert cost == D("15000.00")
        assert realized == D("495")

        snapshot = pnl_snapshot(db, "BTC/USDT", D("70000"), Environment.TESTNET)
        assert snapshot["environment"] == "testnet"
        assert snapshot["realized_pnl_quote"] == "495"
        assert snapshot["unrealized_pnl_quote"] == "2500.00"


def test_pnl_does_not_mix_testnet_and_live_ledgers():
    engine = make_db()
    with Session(engine) as db:
        add_position(db, Environment.TESTNET, "BTC/USDT", D("0.25"), D("15000"))
        add_position(db, Environment.LIVE, "BTC/USDT", D("0.10"), D("6000"))
        add_realized(db, 100, Environment.TESTNET, "BTC/USDT", D("495"))
        add_realized(db, 101, Environment.LIVE, "BTC/USDT", D("77"))
        db.commit()

        assert position_for_symbol(db, "BTC/USDT", Environment.TESTNET) == (
            D("0.25"), D("15000"), D("495")
        )
        assert position_for_symbol(db, "BTC/USDT", Environment.LIVE) == (
            D("0.10"), D("6000"), D("77")
        )


def test_missing_ledger_returns_zero_without_replaying_fills():
    engine = make_db()
    with Session(engine) as db:
        assert position_for_symbol(db, "ETH/USDT", Environment.TESTNET) == (
            D("0"), D("0"), D("0")
        )
