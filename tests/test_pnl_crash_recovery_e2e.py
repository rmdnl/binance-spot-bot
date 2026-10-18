import multiprocessing
import os
from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
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


def _sell_fill_then_commit_and_die(database_url):
    # Simulate a process dying after inventory and realized PNL are committed,
    # but before it can acknowledge successful processing to its caller.
    engine = create_engine(database_url)
    with Session(engine) as db:
        order = db.scalar(
            select(SpotOrder).where(
                SpotOrder.client_order_id == "pnl-crash-sell-001"
            )
        )
        assert order is not None
        fill = SpotFill(
            order_id=order.id,
            symbol="BTC/USDT",
            side="sell",
            price=D("84000"),
            amount=D("0.001"),
            quote_amount=D("84"),
            fee=D("0.084"),
            fee_currency="USDT",
            exchange_trade_id="trade-pnl-crash-sell-001",
        )
        db.add(fill)
        db.flush()
        result = apply_fill(db, fill)
        assert result.applied is True
        assert result.realized_pnl == D("2.835000000000")
        db.commit()
        os._exit(75)


def test_sell_pnl_survives_process_death_and_replay_is_idempotent(tmp_path):
    database_path = tmp_path / "sell-pnl-crash.sqlite"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        position = InventoryPosition(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            base_quantity=D("0.002"),
            quote_cost=D("162.162"),
        )
        order = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="sell",
            order_type="limit",
            status="filled",
            amount=D("0.001"),
            price=D("84000"),
            filled_amount=D("0.001"),
            client_order_id="pnl-crash-sell-001",
            exchange_order_id="exchange-pnl-crash-sell-001",
        )
        db.add_all([position, order])
        db.commit()
    engine.dispose()

    process = multiprocessing.get_context("fork").Process(
        target=_sell_fill_then_commit_and_die,
        args=(database_url,),
    )
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError("sell accounting worker did not exit within 10 seconds")
    assert process.exitcode == 75

    # A fresh engine/session represents recovery after the abrupt process exit.
    engine = create_engine(database_url)
    with Session(engine) as db:
        fill = db.scalar(
            select(SpotFill).where(
                SpotFill.exchange_trade_id == "trade-pnl-crash-sell-001"
            )
        )
        position = db.scalar(
            select(InventoryPosition).where(
                InventoryPosition.environment == Environment.TESTNET.value,
                InventoryPosition.symbol == "BTC/USDT",
            )
        )
        assert fill is not None
        assert position is not None
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1

        pnl = db.scalar(
            select(RealizedPNLEntry).where(RealizedPNLEntry.fill_id == fill.id)
        )
        assert pnl.realized_pnl == D("2.835000000000")
        assert pnl.entry_cost == D("81.081000000000")
        assert pnl.proceeds == D("83.916000000000")
        assert pnl.fees == D("0.084000000000")
        assert position.base_quantity == D("0.001000000000")
        assert position.quote_cost == D("81.081000000000")

        # Replaying the committed fill after restart must not change inventory
        # or create another realized-PNL entry.
        result = apply_fill(db, fill)
        db.commit()
        assert result.applied is False
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1
        db.refresh(position)
        assert position.base_quantity == D("0.001000000000")
        assert position.quote_cost == D("81.081000000000")
        assert db.scalar(
            select(RealizedPNLEntry.realized_pnl).where(
                RealizedPNLEntry.fill_id == fill.id
            )
        ) == D("2.835000000000")

    engine.dispose()
