from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, OrderStatus, SpotOrder, SpotProtection
import spot_bot.spot_protection_monitor as monitor


class FakeExchange:
    def __init__(self, price):
        self.price = price
        self.submitted = []

    def ticker(self, symbol):
        return {"last": self.price}


def test_restart_after_entry_fill_then_trigger_creates_single_exit(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        entry = SpotOrder(environment="testnet", symbol="BTC/USDT", side="buy", order_type="market",
                          status=OrderStatus.FILLED.value, amount=Decimal("0.01"),
                          filled_amount=Decimal("0.01"), average_price=Decimal("100"),
                          client_order_id="entry-e2e", exchange_order_id="100")
        db.add(entry)
        db.flush()
        protection = SpotProtection(entry_order_id=entry.id, environment="testnet", symbol="BTC/USDT",
                                    stop_loss_price=Decimal("90"), take_profit_price=Decimal("110"),
                                    protected_quantity=Decimal("0.01"), status="armed")
        db.add(protection)
        db.commit()
        protection_id = protection.id

    fake = FakeExchange("89")

    def create_exit(db, request, risk_reducing=False):
        assert risk_reducing
        order = SpotOrder(environment="testnet", symbol=request.symbol, side="sell", order_type="market",
                          status=OrderStatus.NEW.value, amount=request.amount,
                          filled_amount=Decimal("0"), client_order_id=f"exit-{len(fake.submitted)+1}")
        db.add(order)
        db.flush()
        fake.submitted.append(order.id)
        return order

    def recover(db, adapter, order, risk_reducing=False):
        assert risk_reducing
        order.status = OrderStatus.FILLED.value
        order.filled_amount = order.amount
        order.average_price = Decimal("89")
        return SimpleNamespace(order=order, exchange=None)

    monkeypatch.setattr(monitor, "create_intent", create_exit)
    monkeypatch.setattr(monitor, "recover_or_submit", recover)
    monkeypatch.setattr(monitor, "reconcile_order", lambda db, adapter, order: order)
    monkeypatch.setattr(monitor, "record_fills", lambda db, adapter, order: None)

    def factory():
        return Session(engine)

    # First invocation represents the worker before a process restart.
    monitor.monitor_spot_protections_once(factory, lambda env: fake)
    # Second invocation represents a fresh worker after restart.
    monitor.monitor_spot_protections_once(factory, lambda env: fake)

    with Session(engine) as db:
        row = db.get(SpotProtection, protection_id)
        assert row.status == "closed"
        assert row.trigger_reason == "stop_loss"
        assert row.exit_order_id is not None
        assert db.scalar(select(func.count()).select_from(SpotOrder).where(SpotOrder.side == "sell")) == 1
    assert len(fake.submitted) == 1
    engine.dispose()
