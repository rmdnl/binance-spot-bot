from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from spot_bot.models import Base, OrderSide, OrderStatus, OrderType, SpotOrder, SpotProtection
import spot_bot.spot_protection_monitor as monitor


class FakeAdapter:
    def __init__(self, environment, price="95"):
        self.price = price

    def ticker(self, symbol):
        return {"last": self.price}


def setup_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'protection.sqlite'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def add_protection(factory, *, price="95", sl="90", tp="110"):
    with factory() as db:
        entry = SpotOrder(
            environment="testnet", symbol="BTC/USDT", side="buy", order_type="market",
            status=OrderStatus.FILLED.value, amount=Decimal("0.1"),
            filled_amount=Decimal("0.1"), average_price=Decimal("100"),
            client_order_id="entry-client", exchange_order_id="entry-exchange",
        )
        db.add(entry)
        db.flush()
        protection = SpotProtection(
            entry_order_id=entry.id, environment="testnet", symbol="BTC/USDT",
            stop_loss_price=Decimal(sl) if sl else None,
            take_profit_price=Decimal(tp) if tp else None,
            protected_quantity=Decimal("0.1"), status="armed",
        )
        db.add(protection)
        db.commit()
        return protection.id


def install_exit_fakes(monkeypatch):
    submitted = []

    def create_intent(db, request, risk_reducing=False):
        assert risk_reducing is True
        assert request.side == OrderSide.SELL
        assert request.order_type == OrderType.MARKET
        order = SpotOrder(
            environment=request.environment.value, symbol=request.symbol, side="sell",
            order_type="market", status=OrderStatus.NEW.value, amount=request.amount,
            filled_amount=Decimal("0"), client_order_id=f"exit-client-{len(submitted)+1}",
        )
        db.add(order)
        db.flush()
        submitted.append(order.id)
        return order

    def recover_or_submit(db, adapter, order, risk_reducing=False):
        assert risk_reducing is True
        order.status = OrderStatus.FILLED.value
        order.filled_amount = order.amount
        db.flush()
        return order

    monkeypatch.setattr(monitor, "create_intent", create_intent)
    monkeypatch.setattr(monitor, "recover_or_submit", recover_or_submit)
    monkeypatch.setattr(monitor, "reconcile_order", lambda db, adapter, order: order)
    monkeypatch.setattr(monitor, "record_fills", lambda db, adapter, order: None)
    return submitted


def test_stop_loss_creates_one_persistent_market_exit_and_closes(monkeypatch, tmp_path):
    engine, factory = setup_db(tmp_path)
    protection_id = add_protection(factory)
    submitted = install_exit_fakes(monkeypatch)
    monitor.monitor_spot_protections_once(factory, lambda env: FakeAdapter(env, "89"))
    with factory() as db:
        protection = db.get(SpotProtection, protection_id)
        assert protection.status == "closed"
        assert protection.trigger_reason == "stop_loss"
        assert protection.exit_order_id is not None
        assert abs(protection.protected_quantity - Decimal("0.1")) < Decimal("0.000000000001")
    assert len(submitted) == 1
    monitor.monitor_spot_protections_once(factory, lambda env: FakeAdapter(env, "89"))
    assert len(submitted) == 1
    engine.dispose()


def test_take_profit_triggers_at_or_above_threshold(monkeypatch, tmp_path):
    engine, factory = setup_db(tmp_path)
    protection_id = add_protection(factory, price="111")
    install_exit_fakes(monkeypatch)
    monitor.monitor_spot_protections_once(factory, lambda env: FakeAdapter(env, "111"))
    with factory() as db:
        protection = db.get(SpotProtection, protection_id)
        assert protection.status == "closed"
        assert protection.trigger_reason == "take_profit"
    engine.dispose()


def test_protection_waits_when_price_is_inside_range(monkeypatch, tmp_path):
    engine, factory = setup_db(tmp_path)
    protection_id = add_protection(factory)
    submitted = install_exit_fakes(monkeypatch)
    monitor.monitor_spot_protections_once(factory, lambda env: FakeAdapter(env, "95"))
    with factory() as db:
        protection = db.get(SpotProtection, protection_id)
        assert protection.status == "armed"
        assert protection.last_price == Decimal("95.000000000000000000")
        assert protection.exit_order_id is None
    assert submitted == []
    engine.dispose()
