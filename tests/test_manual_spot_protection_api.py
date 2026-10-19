from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spot_bot import main, spot_api
from spot_bot.db import get_db
from spot_bot.models import Base, SpotOrder, SpotProtection


class FakeAdapter:
    def __init__(self, environment):
        self.environment = environment

    def ticker(self, symbol):
        return {"last": "100"}


def test_manual_buy_persists_sl_tp_before_order_submission(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-spot.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    monkeypatch.setattr(spot_api, "require_auth", lambda request: None)
    monkeypatch.setattr(spot_api, "require_csrf", lambda request, token: None)
    monkeypatch.setattr(spot_api, "BinanceSpot", FakeAdapter)
    submitted = []

    def fake_create_intent(db, request, **kwargs):
        order = SpotOrder(
            environment=request.environment.value, symbol=request.symbol, side=request.side.value,
            order_type=request.order_type.value, status="new", amount=request.amount,
            price=request.price, filled_amount=Decimal("0"), client_order_id="manual-test-client",
        )
        db.add(order)
        db.flush()
        submitted.append(order.id)
        return order

    def fake_recover(db, adapter, order):
        order.status = "filled"
        order.filled_amount = order.amount
        order.average_price = Decimal("100")
        order.exchange_order_id = "test-exchange-order"
        db.flush()
        return SimpleNamespace(order=order, exchange={"status": "closed"})

    monkeypatch.setattr(spot_api, "create_intent", fake_create_intent)
    monkeypatch.setattr(spot_api, "recover_or_submit", fake_recover)
    monkeypatch.setattr(spot_api, "record_fills", lambda db, adapter, order: None)
    main.app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(main.app).post("/api/spot/orders", data={
            "environment": "testnet", "symbol": "BTC/USDT", "side": "buy",
            "order_type": "market", "amount": "0.01",
            "stop_loss_price": "90", "take_profit_price": "110", "csrf_token": "test",
        })
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "filled"
    assert payload["protection"]["status"] == "waiting_entry"
    assert payload["protection"]["stop_loss_price"] == "90.000000000000000000"
    assert payload["protection"]["take_profit_price"] == "110.000000000000000000"
    assert len(submitted) == 1


def test_manual_buy_rejects_stop_loss_above_market_reference(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-spot-invalid.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    monkeypatch.setattr(spot_api, "require_auth", lambda request: None)
    monkeypatch.setattr(spot_api, "require_csrf", lambda request, token: None)
    monkeypatch.setattr(spot_api, "BinanceSpot", FakeAdapter)
    monkeypatch.setattr(spot_api, "create_intent", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("order intent must not be created")))
    main.app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(main.app).post("/api/spot/orders", data={
            "environment": "testnet", "symbol": "BTC/USDT", "side": "buy",
            "order_type": "market", "amount": "0.01",
            "stop_loss_price": "105", "csrf_token": "test",
        })
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 400
    assert "stop loss must be below" in response.json()["detail"]
