from decimal import Decimal as D

from dca_bot.models import Environment, OrderRequest, OrderSide, OrderType
from dca_bot.exchange import BinanceSpot, ExchangeSafetyError


def test_create_order_fails_closed_when_submission_disabled():
    # Avoid network initialization: exercise the guard with a minimally constructed adapter.
    adapter = object.__new__(BinanceSpot)
    adapter.allow_order_submission = False
    adapter.environment = Environment.TESTNET
    request = OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.MARKET, D("0.001"))
    try:
        adapter.create_order(request)
    except ExchangeSafetyError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("Order submission must be disabled")
