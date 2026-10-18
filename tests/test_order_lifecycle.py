from decimal import Decimal as D

import ccxt
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderStatus, OrderType, SpotOrder, SpotFill
from spot_bot.exchange import BinanceSpot, ExchangeSafetyError
from spot_bot.order_service import cancel_order, create_intent, record_fills, recover_or_submit, reconcile_order


class FakeExchange:
    def market_id(self, symbol):
        return symbol.replace("/", "")

    def market(self, symbol):
        return {
            "spot": True,
            "base": symbol.split("/")[0],
            "quote": symbol.split("/")[1],
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 10}},
            "info": {"filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]},
        }

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.6f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.2f}"


class FakeAdapter:
    def __init__(self, existing=None, lookup_error=None):
        self.existing = existing
        self.lookup_error = lookup_error
        self.submissions = 0
        self.cancel_calls = 0
        self.trades = []

    def fetch_order_by_client_id(self, symbol, client_id):
        if self.lookup_error:
            raise self.lookup_error
        if self.existing is None:
            raise ccxt.OrderNotFound("not found")
        return self.existing

    def validate_order(self, request):
        return None

    def create_order(self, request):
        self.submissions += 1
        return {"id": "123", "status": "open", "filled": "0", "average": None}

    def fetch_order(self, exchange_order_id, symbol):
        return self.existing or {"id": exchange_order_id, "status": "open", "filled": "0", "average": None}

    def cancel_order(self, exchange_order_id, symbol):
        self.cancel_calls += 1
        return {"id": exchange_order_id, "status": "canceled", "filled": "0", "average": None}

    def recent_trades(self, symbol, limit=100):
        return self.trades



class AmbiguousSubmitAdapter(FakeAdapter):
    """Exchange accepts the order but the client loses the response."""

    def __init__(self):
        super().__init__()
        self.lookup_calls = 0

    def fetch_order_by_client_id(self, symbol, client_id):
        self.lookup_calls += 1
        if self.lookup_calls == 1:
            raise ccxt.OrderNotFound("not found before submit")
        if self.existing is None:
            raise ccxt.OrderNotFound("not found after ambiguous submit")
        return self.existing

    def create_order(self, request):
        self.submissions += 1
        self.existing = {
            "id": "accepted-before-timeout",
            "clientOrderId": request.client_order_id,
            "symbol": request.symbol,
            "status": "open",
            "filled": "0",
            "average": None,
        }
        raise ccxt.RequestTimeout("exchange accepted order but response timed out")


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def make_order(db):
    return create_intent(db, OrderRequest(
        Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT,
        D("0.01"), D("80000")
    ))


def test_binance_filters_reject_bad_amount_and_notional():
    adapter = object.__new__(BinanceSpot)
    adapter.environment = Environment.TESTNET
    adapter.exchange = FakeExchange()
    with pytest.raises(ExchangeSafetyError):
        adapter.validate_order(OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.0001"), D("80000")))
    with pytest.raises(ExchangeSafetyError):
        adapter.validate_order(OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.001"), D("1000")))


def test_client_order_id_is_36_chars():
    db = db_session()
    order = make_order(db)
    assert len(order.client_order_id) == 36


def test_duplicate_client_order_id_is_rejected():
    db = db_session()
    first = make_order(db)
    db.commit()
    with pytest.raises(ValueError, match="already exists"):
        create_intent(db, OrderRequest(
            Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT,
            D("0.01"), D("80000"), first.client_order_id,
        ))
    db.rollback()
    assert db.scalar(select(SpotOrder).where(SpotOrder.client_order_id == first.client_order_id)).id == first.id


def test_recovery_uses_existing_order_and_does_not_resubmit():
    db = db_session()
    order = make_order(db)
    db.commit()
    adapter = FakeAdapter(existing={"id": "999", "status": "open", "filled": "0", "average": None})
    result = recover_or_submit(db, adapter, order)
    assert result.order.exchange_order_id == "999"
    assert result.order.status == OrderStatus.OPEN.value
    assert adapter.submissions == 0



def test_ambiguous_submit_recovers_accepted_order_without_duplicate_submission():
    db = db_session()
    order = make_order(db)
    db.commit()
    adapter = AmbiguousSubmitAdapter()

    result = recover_or_submit(db, adapter, order)

    assert result.order.exchange_order_id == "accepted-before-timeout"
    assert result.order.status == OrderStatus.OPEN.value
    assert adapter.submissions == 1
    assert adapter.lookup_calls == 2
    assert result.order.error_message is None


def test_unknown_lookup_error_fails_closed_without_resubmit():
    db = db_session()
    order = make_order(db)
    db.commit()
    adapter = FakeAdapter(lookup_error=ccxt.NetworkError("timeout"))
    with pytest.raises(ccxt.NetworkError):
        recover_or_submit(db, adapter, order)
    assert adapter.submissions == 0
    db.rollback()
    persisted = db.get(SpotOrder, order.id)
    assert persisted is not None
    assert persisted.status == OrderStatus.NEW.value


def test_order_status_mapping_accepts_binance_new():
    from spot_bot.order_service import _map_status
    assert _map_status("new") == OrderStatus.NEW.value
    assert _map_status("open") == OrderStatus.OPEN.value
    assert _map_status("closed") == OrderStatus.FILLED.value


def test_reconcile_records_fills_and_is_idempotent():
    db = db_session()
    order = make_order(db)
    order.exchange_order_id = "123"
    order.status = OrderStatus.OPEN.value
    db.commit()
    adapter = FakeAdapter(existing={"id": "123", "status": "closed", "filled": "0.01", "average": "80100"})
    adapter.trades = [{
        "id": "trade-1", "order": "123", "amount": "0.01", "price": "80100",
        "cost": "801", "fee": {"cost": "0.08", "currency": "USDT"},
    }]
    reconcile_order(db, adapter, order)
    assert order.status == OrderStatus.FILLED.value
    assert order.filled_amount == D("0.01")
    assert order.fee_quote == D("0.08")
    assert db.scalar(select(SpotFill).where(SpotFill.exchange_trade_id == "trade-1")).order_id == order.id
    assert record_fills(db, adapter, order) == 0


def test_reconcile_preserves_partial_fill_quantity():
    db = db_session()
    order = make_order(db)
    order.exchange_order_id = "123"
    order.status = OrderStatus.OPEN.value
    db.commit()
    adapter = FakeAdapter(existing={"id": "123", "status": "open", "filled": "0.004", "average": "79950"})
    adapter.trades = [{
        "id": "trade-partial", "order": "123", "amount": "0.004", "price": "79950",
        "cost": "319.8", "fee": {"cost": "0.03", "currency": "USDT"},
    }]
    reconcile_order(db, adapter, order)
    assert order.status == OrderStatus.OPEN.value
    assert order.filled_amount == D("0.004")
    assert order.amount - order.filled_amount == D("0.006")
    fills = db.scalars(select(SpotFill).where(SpotFill.order_id == order.id)).all()
    assert sum((fill.amount for fill in fills), D("0")) == D("0.004")


def test_cancel_updates_local_state_and_is_single_exchange_call():
    db = db_session()
    order = make_order(db)
    order.exchange_order_id = "123"
    order.status = OrderStatus.OPEN.value
    db.commit()
    adapter = FakeAdapter()
    cancel_order(db, adapter, order)
    assert order.status == OrderStatus.CANCELED.value
    assert adapter.cancel_calls == 1


def test_cancel_does_not_allow_terminal_order():
    db = db_session()
    order = make_order(db)
    order.status = OrderStatus.FILLED.value
    order.exchange_order_id = "123"
    db.commit()
    with pytest.raises(ValueError, match="cannot be canceled"):
        cancel_order(db, FakeAdapter(), order)
