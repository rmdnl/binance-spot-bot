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


def open_engine(path):
    return create_engine(f"sqlite:///{path}")


def test_file_backed_process_restart_preserves_inventory_and_pnl_exactly_once(tmp_path):
    database_path = tmp_path / "spot-bot-restart.sqlite"
    engine = open_engine(database_path)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        db.add(
            InventoryPosition(
                environment=Environment.TESTNET.value,
                symbol="BTC/USDT",
                base_asset="BTC",
                quote_asset="USDT",
                base_quantity=D("0"),
                quote_cost=D("0"),
            )
        )
        buy = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            order_type="limit",
            status="filled",
            amount=D("0.02"),
            price=D("80000"),
            client_order_id="restart-durable-buy",
            exchange_order_id="restart-durable-ex-buy",
        )
        sell = SpotOrder(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="sell",
            order_type="limit",
            status="filled",
            amount=D("0.01"),
            price=D("81000"),
            client_order_id="restart-durable-sell",
            exchange_order_id="restart-durable-ex-sell",
        )
        db.add_all([buy, sell])
        db.flush()
        buy_fill = SpotFill(
            order_id=buy.id,
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="buy",
            price=D("80000"),
            amount=D("0.02"),
            quote_amount=D("1600"),
            fee=D("0.16"),
            fee_currency="USDT",
            exchange_trade_id="restart-durable-trade-buy",
        )
        sell_fill = SpotFill(
            order_id=sell.id,
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            side="sell",
            price=D("81000"),
            amount=D("0.01"),
            quote_amount=D("810"),
            fee=D("0.081"),
            fee_currency="USDT",
            exchange_trade_id="restart-durable-trade-sell",
        )
        db.add_all([buy_fill, sell_fill])
        db.commit()
        buy_fill_id = buy_fill.id
        sell_fill_id = sell_fill.id

        # First process applies the buy and commits durable accounting.
        assert apply_fill(db, db.get(SpotFill, buy_fill_id)).applied is True
        db.commit()

    # Simulate a real process restart: dispose the old engine and open the same
    # on-disk SQLite database with a fresh engine and fresh ORM identity map.
    engine.dispose()
    engine = open_engine(database_path)

    with Session(engine) as db:
        assert apply_fill(db, db.get(SpotFill, buy_fill_id)).applied is False
        assert apply_fill(db, db.get(SpotFill, sell_fill_id)).applied is True
        db.commit()

    engine.dispose()
    engine = open_engine(database_path)

    with Session(engine) as db:
        # Replaying both fills after another restart must not duplicate accounting.
        assert apply_fill(db, db.get(SpotFill, buy_fill_id)).applied is False
        assert apply_fill(db, db.get(SpotFill, sell_fill_id)).applied is False
        db.commit()

        position = db.scalar(
            select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT")
        )
        assert position is not None
        assert position.base_quantity == D("0.010000000000")
        assert position.quote_cost == D("800.080000000000")
        assert db.scalar(select(func.count()).select_from(SpotFill)) == 2
        assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 2
        assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 1
        pnl = db.scalar(select(RealizedPNLEntry))
        assert pnl is not None
        assert pnl.realized_pnl == D("9.839000000000")

    engine.dispose()
