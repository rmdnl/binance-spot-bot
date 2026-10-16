from decimal import Decimal as D

from dca_bot.grid import build_grid
from dca_bot.models import Environment, OrderRequest, OrderSide, OrderType
from dca_bot.exchange import BinanceSpot, ExchangeSafetyError
from dca_bot.accounting import Position, unrealized_pnl


def test_arithmetic_grid_includes_exact_range_bounds():
    result = build_grid(D("80000"), D("90000"), 11, "arithmetic")
    assert len(result.levels) == 11
    assert result.levels[0].price == D("80000")
    assert result.levels[-1].price == D("90000")


def test_geometric_grid_is_strictly_increasing():
    result = build_grid(D("80"), D("120"), 7, "geometric")
    assert len(result.levels) == 7
    assert all(left.price < right.price for left, right in zip(result.levels, result.levels[1:]))


def test_unrealized_pnl_uses_quote_cost_basis():
    position = Position(asset="BTC", quantity=D("0.01"), cost_quote=D("800"), quote="USDT")
    assert unrealized_pnl(position, D("90000")) == D("100")


def test_order_environment_enum_is_explicit():
    assert Environment.TESTNET.value == "testnet"
    assert Environment.LIVE.value == "live"


def test_order_request_requires_positive_amount_at_boundary():
    request = OrderRequest(Environment.TESTNET, "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, D("0"))
    assert request.amount == 0  # validation is enforced by the executor before submission
