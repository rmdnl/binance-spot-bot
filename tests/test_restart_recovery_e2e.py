from decimal import Decimal as D

import ccxt
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.grid_engine import initial_order_plans, submit_grid_order
from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    GridBot,
    GridLevel,
    InventoryApplication,
    InventoryPosition,
    OrderStatus,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)
from spot_bot.order_service import recover_or_submit, record_fills


class CrashAfterExchangeAdapter:
    def __init__(self):
        self.submissions = []
        self.orders = {}
        self.recovery_lookups = 0
        self.crash_once = True
        self.trades = []

    def validate_order(self, request):
        return None

    def create_order(self, request):
        order_id = f"exchange-{len(self.submissions) + 1}"
        raw = {
            "id": order_id,
            "status": "open",
            "filled": "0",
            "average": None,
            "fee": {"cost": "0", "currency": "USDT"},
        }
        self.submissions.append(request)
        self.orders[order_id] = raw
        if self.crash_once:
            self.crash_once = False
            raise ConnectionError("simulated process crash after exchange accepted order")
        return raw

    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.recovery_lookups += 1
        if self.orders:
            return next(iter(self.orders.values()))
        raise ccxt.OrderNotFound("not found")

    def recent_trades(self, symbol, limit=100):
        return self.trades


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_bot(db):
    bot = GridBot(
        environment="testnet",
        symbol="BTC/USDT",
        grid_type="arithmetic",
        lower_price=D("80000"),
        upper_price=D("90000"),
        grids=11,
        capital_quote=D("500"),
        max_open_orders=1,
        status="running",
    )
    db.add(bot)
    db.flush()
    for index in range(11):
        db.add(
            GridLevel(
                bot_id=bot.id,
                level_index=index,
                price=D(80000 + index * 1000),
            )
        )
    db.commit()
    return bot


def test_restart_recovers_exchange_accepted_order_without_duplicate_submission():
    db = db_session()
    bot = make_bot(db)
    adapter = CrashAfterExchangeAdapter()
    plan = initial_order_plans(bot, D("85000"))[0]

    try:
        submit_grid_order(db, adapter, bot, plan)
    except ConnectionError:
        db.rollback()

    order = db.scalar(select(SpotOrder).order_by(SpotOrder.id.desc()))
    assert order is not None
    assert order.status == OrderStatus.NEW.value
    assert len(adapter.submissions) == 1

    recovered = recover_or_submit(db, adapter, order, post_only=True)
    db.commit()

    assert recovered.exchange is not None
    assert recovered.order.exchange_order_id == "exchange-1"
    assert len(adapter.submissions) == 1
    assert adapter.recovery_lookups == 2


def test_restart_recovery_preserves_fill_accounting_when_exchange_order_already_exists():
    db = db_session()
    adapter = CrashAfterExchangeAdapter()
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status=OrderStatus.NEW.value,
        amount=D("0.01"),
        price=D("80000"),
        client_order_id="spot-restart-e2e-001",
    )
    db.add(order)
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
    db.commit()
    adapter.orders["exchange-1"] = {
        "id": "exchange-1",
        "status": "closed",
        "filled": "0.01",
        "average": "80000",
        "fee": {"cost": "0.08", "currency": "USDT"},
    }
    adapter.trades = [
        {
            "id": "trade-restart-1",
            "order": "exchange-1",
            "amount": "0.01",
            "price": "80000",
            "cost": "800",
            "fee": {"cost": "0.08", "currency": "USDT"},
        }
    ]

    recovered = recover_or_submit(db, adapter, order, post_only=True)
    db.commit()
    assert recovered.exchange is not None
    assert recovered.order.status == OrderStatus.FILLED.value

    fills = db.scalars(select(SpotFill).where(SpotFill.order_id == order.id)).all()
    assert len(fills) == 1
    first = apply_fill(db, fills[0])
    db.commit()
    assert first.applied is True
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0

    # Reconciliation after restart must see the same exchange trade but create no duplicate fill.
    assert record_fills(db, adapter, order) == 0
    db.commit()
    assert db.scalar(select(func.count()).select_from(SpotFill)) == 1
    assert db.scalar(select(func.count()).select_from(InventoryApplication)) == 1
    assert db.scalar(select(func.count()).select_from(RealizedPNLEntry)) == 0
    position = db.scalar(select(InventoryPosition).where(InventoryPosition.symbol == "BTC/USDT"))
    assert position.base_quantity == D("0.010000000000")
    assert position.quote_cost == D("800.080000000000")
    assert len(adapter.submissions) == 0
