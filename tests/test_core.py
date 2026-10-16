from decimal import Decimal

from dca_bot.grid import build_grid
from dca_bot.accounting import Position, unrealized_pnl


def test_arithmetic_grid():
    preview = build_grid(Decimal("80000"), Decimal("90000"), 11, "arithmetic")
    assert len(preview.levels) == 11
    assert preview.levels[0].price == Decimal("80000.000000000000")
    assert preview.levels[-1].price == Decimal("90000.000000000000")


def test_geometric_grid():
    preview = build_grid(Decimal("80000"), Decimal("90000"), 11, "geometric")
    assert len(preview.levels) == 11
    assert preview.levels[0].price == Decimal("80000.000000000000")
    assert preview.levels[-1].price <= Decimal("90000")


def test_unrealized_pnl():
    position = Position("BTC", Decimal("0.01"), Decimal("800"), "USDT")
    assert unrealized_pnl(position, Decimal("90000")) == Decimal("100")
