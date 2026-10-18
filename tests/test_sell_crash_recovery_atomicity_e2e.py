import multiprocessing
import os
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import record_fills


class SellCrashAdapter:
    def __init__(self, trade):
        self.trade = trade

    def recent_trades(self, symbol, limit=100):
        return [self.trade]


def sqlite_engine(url):
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def disable_driver_transaction_control(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def emit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


def _process_sell_then_die(database_url, trade, commit_before_exit):
    engine = sqlite_engine(database_url)
    with Session(engine) as db:
        order = db.scalar(
            select(SpotOrder).where(
                SpotOrder.client_order_id == "sell-crash-atomicity"
            )
        )
        assert order is not None
        assert record_fills(db, SellCrashAdapter(trade), order) == 1
        if commit_before_exit:
            db.commit()
        # Simulate abrupt process death either before or immediately after commit.
        os._exit(81 if commit_before_exit else 80)


def _counts(db):
    return {
        "fills": db.scalar(select(func.count()).select_from(SpotFill)),
        "applications": db.scalar(
            select(func.count()).select_from(InventoryApplication)
        ),
        "pnl": db.scalar(select(func.count()).select_from(RealizedPNLEntry)),
    }


@pytest.mark.parametrize(
    ("commit_before_exit", "exit_code"),
    [(False, 80), (True, 81)],
    ids=["crash-before-commit", "crash-after-commit-before-ack"],
)
def test_sell_fill_inventory_and_realized_pnl_recover_atomically(
    tmp_path, commit_before_exit, exit_code
):
    database_path = tmp_path / f"sell-crash-{exit_code}.sqlite"
    database_url = f"sqlite:///{database_path}"
    engine = sqlite_engine(database_url)
    Base.metadata.create_all(engine)

    buy_trade = {
        "id": "sell-crash-buy-trade",
        "order": "sell-crash-buy-exchange",
        "amount": "0.02",
        "price": "80000",
        "cost": "1600",
        "fee": {"cost": "0.16", "currency": "USDT"},
    }
    sell_trade = {
        "id": "sell-crash-sell-trade",
        "order": "sell-crash-sell-exchange",
        "amount": "0.01",
        "price": "81000",
        "cost": "810",
        "fee": {"cost": "0.081", "currency": "USDT"},
    }

    with Session(engine) as db:
        buy_order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            status="filled",
            amount=D("0.02"),
            price=D("80000"),
            client_order_id="sell-crash-buy",
            exchange_order_id="sell-crash-buy-exchange",
        )
        sell_order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="sell",
            order_type="limit",
            status="filled",
            amount=D("0.01"),
            price=D("81000"),
            client_order_id="sell-crash-atomicity",
            exchange_order_id="sell-crash-sell-exchange",
        )
        db.add_all([buy_order, sell_order])
        db.flush()
        buy_fill = SpotFill(
            order_id=buy_order.id,
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            price=D("80000"),
            amount=D("0.02"),
            quote_amount=D("1600"),
            fee=D("0.16"),
            fee_currency="USDT",
            exchange_trade_id=buy_trade["id"],
        )
        db.add(buy_fill)
        db.flush()
        apply_fill(db, buy_fill)
        db.commit()

    engine.dispose()

    process = multiprocessing.get_context("fork").Process(
        target=_process_sell_then_die,
        args=(database_url, sell_trade, commit_before_exit),
    )
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError("sell crash worker did not exit within 10 seconds")
    assert process.exitcode == exit_code

    engine = sqlite_engine(database_url)
    with Session(engine) as db:
        position = db.scalar(
            select(InventoryPosition).where(
                InventoryPosition.environment == Environment.TESTNET.value,
                InventoryPosition.symbol == "BTC/USDT",
            )
        )
        assert position is not None

        if commit_before_exit:
            # The commit succeeded despite the process dying before acknowledging it.
            assert _counts(db) == {"fills": 2, "applications": 1, "pnl": 1}
            assert position.base_quantity == D("0.010000000000")
            assert position.quote_cost == D("800.080000000000")
            pnl = db.scalar(select(RealizedPNLEntry))
            assert pnl is not None
            assert pnl.realized_pnl == D("9.839000000000")
        else:
            # No partial sell fill, inventory mutation, or PNL may survive rollback.
            assert _counts(db) == {"fills": 1, "applications": 1, "pnl": 0}
            assert position.base_quantity == D("0.020000000000")
            assert position.quote_cost == D("1600.160000000000")

            sell_order = db.scalar(
                select(SpotOrder).where(
                    SpotOrder.client_order_id == "sell-crash-atomicity"
                )
            )
            assert record_fills(db, SellCrashAdapter(sell_trade), sell_order) == 1
            db.commit()
            db.refresh(position)
            assert _counts(db) == {"fills": 2, "applications": 2, "pnl": 1}
            assert position.base_quantity == D("0.010000000000")
            assert position.quote_cost == D("800.080000000000")
            pnl = db.scalar(select(RealizedPNLEntry))
            assert pnl is not None
            assert pnl.realized_pnl == D("9.839000000000")

        # A further restart/replay must never apply the sell or PNL a second time.
        sell_order = db.scalar(
            select(SpotOrder).where(
                SpotOrder.client_order_id == "sell-crash-atomicity"
            )
        )
        assert record_fills(db, SellCrashAdapter(sell_trade), sell_order) == 0
        db.commit()
        db.refresh(position)
        assert _counts(db) == {"fills": 2, "applications": 2, "pnl": 1}
        assert position.base_quantity == D("0.010000000000")
        assert position.quote_cost == D("800.080000000000")
        pnl = db.scalar(select(RealizedPNLEntry))
        assert pnl is not None
        assert pnl.realized_pnl == D("9.839000000000")

    engine.dispose()
