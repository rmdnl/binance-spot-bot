from decimal import Decimal as D

import ccxt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spot_bot.models import Base, Environment, OrderRequest, OrderSide, OrderStatus, OrderType
from spot_bot.exchange import BinanceSpot, ExchangeSafetyError
from spot_bot.order_service import create_intent, recover_or_submit


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


def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


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
    order = create_intent(db, OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000")))
    assert len(order.client_order_id) == 36


def test_recovery_uses_existing_order_and_does_not_resubmit():
    db = db_session()
    order = create_intent(db, OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000")))
    adapter = FakeAdapter(existing={"id": "999", "status": "open", "filled": "0", "average": None})
    result = recover_or_submit(db, adapter, order)
    assert result.order.exchange_order_id == "999"
    assert adapter.submissions == 0


def test_unknown_lookup_error_fails_closed_without_resubmit():
    db = db_session()
    order = create_intent(db, OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0.01"), D("80000")))
    adapter = FakeAdapter(lookup_error=ccxt.NetworkError("timeout"))
    with pytest.raises(ccxt.NetworkError):
        recover_or_submit(db, adapter, order)
    assert adapter.submissions == 0


def test_order_status_mapping_accepts_binance_new():
    from spot_bot.order_service import _map_status
    assert _map_status("new") == OrderStatus.NEW.value
    assert _map_status("open") == OrderStatus.OPEN.value
    assert _map_status("closed") == OrderStatus.FILLED.value
