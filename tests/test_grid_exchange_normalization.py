from decimal import Decimal as D

import pytest

from spot_bot.grid_engine import _normalize_grid_request
from spot_bot.models import Environment, OrderRequest, OrderSide, OrderType


class FakeExchange:
    def amount_to_precision(self, symbol, amount):
        return f"{D(str(amount)).quantize(D('0.001')):.3f}"

    def price_to_precision(self, symbol, price):
        return f"{D(str(price)).quantize(D('0.01')):.2f}"


class FakeAdapter:
    exchange = FakeExchange()

    def market(self, symbol):
        return {
            "spot": True,
            "limits": {"amount": {"min": "0.001"}, "cost": {"min": "10"}},
            "info": {"filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]},
        }


def request(amount="0.1234", price="100.009"):
    return OrderRequest(
        environment=Environment.TESTNET,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        amount=D(amount),
        price=D(price),
        post_only=True,
    )


def test_grid_request_is_normalized_before_persistence():
    prepared = _normalize_grid_request(FakeAdapter(), request())
    assert prepared.amount == D("0.123")
    assert prepared.price == D("100.01")
    assert prepared.post_only is True


def test_min_notional_uses_rounded_values():
    with pytest.raises(ValueError, match="notional"):
        _normalize_grid_request(FakeAdapter(), request(amount="0.0994", price="100.009"))


def test_zero_after_precision_is_rejected():
    with pytest.raises(ValueError, match="rounds to zero"):
        _normalize_grid_request(FakeAdapter(), request(amount="0.0004"))
