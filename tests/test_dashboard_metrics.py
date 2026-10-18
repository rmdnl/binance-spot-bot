from datetime import UTC, datetime
from decimal import Decimal as D

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spot_bot.dashboard_metrics import dashboard_metrics
from spot_bot.models import Base, Environment, InventoryPosition, RealizedPNLEntry


class FakeExchange:
    markets = {"BTC/USDT": {}, "BNB/USDT": {}}


class FakeAdapter:
    exchange = FakeExchange()

    def balance(self):
        return {"total": {"USDT": 100, "BTC": 0.1, "BNB": 2, "UNKNOWN": 10, "ZERO": 0}}

    def ticker(self, symbol):
        prices = {"BTC/USDT": "50000", "BNB/USDT": "300"}
        return {"last": prices[symbol]}


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def add_position(db, symbol, quantity, cost, environment=Environment.TESTNET.value):
    db.add(InventoryPosition(
        environment=environment,
        symbol=symbol,
        base_asset=symbol.split("/")[0],
        quote_asset=symbol.split("/")[1],
        base_quantity=D(quantity),
        quote_cost=D(cost),
    ))


def add_realized(db, fill_id, symbol, pnl, environment=Environment.TESTNET.value):
    db.add(RealizedPNLEntry(
        fill_id=fill_id,
        environment=environment,
        symbol=symbol,
        quantity=D("1"),
        entry_cost=D("1"),
        proceeds=D("1"),
        fees=D("0"),
        realized_pnl=D(pnl),
        timestamp=datetime.now(UTC),
    ))


def test_dashboard_metrics_calculate_equity_and_pnl_and_flag_unpriced_assets():
    engine = make_db()
    with Session(engine) as db:
        add_position(db, "BTC/USDT", "0.1", "4500")
        add_position(db, "BNB/USDT", "2", "700")
        add_position(db, "BTC/USDT", "99", "1", Environment.LIVE.value)
        add_realized(db, 1, "BTC/USDT", "10")
        add_realized(db, 2, "BNB/USDT", "-2")
        add_realized(db, 3, "BTC/USDT", "999", Environment.LIVE.value)
        db.commit()

        result = dashboard_metrics(db, FakeAdapter(), Environment.TESTNET)

    assert D(result["equity_usdt"]) == D("5700.0")
    assert result["equity_complete"] is False
    assert result["unpriced_assets"] == ["UNKNOWN"]
    assert D(result["realized_pnl_usdt"]) == D("8")
    assert result["realized_pnl_complete"] is True
    assert D(result["unrealized_pnl_usdt"]) == D("400.0")
    assert result["unrealized_pnl_complete"] is True
