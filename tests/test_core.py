import unittest
from decimal import Decimal as D
from types import SimpleNamespace

from dca_bot.grid import build_grid
from dca_bot.risk import quote_asset, reserved_capital_by_quote, reserved_capital_quote, RiskLimits, RiskSnapshot, check_risk


class CoreTests(unittest.TestCase):
    def test_grid_levels_include_bounds(self):
        preview = build_grid(D("80000"), D("90000"), 11, "arithmetic")
        self.assertEqual(len(preview.levels), 11)
        self.assertEqual(preview.levels[0].price, D("80000"))
        self.assertEqual(preview.levels[-1].price, D("90000"))

    def test_geometric_levels_monotonic(self):
        preview = build_grid(D("80"), D("120"), 7, "geometric")
        self.assertEqual(len(preview.levels), 7)
        self.assertTrue(all(a.price < b.price for a, b in zip(preview.levels, preview.levels[1:])))

    def test_quote_asset_requires_explicit_pair(self):
        self.assertEqual(quote_asset("ETH/BTC"), "BTC")
        with self.assertRaises(ValueError):
            quote_asset("ETHBTC")

    def test_capital_reservation_is_quote_aware(self):
        deals = [
            SimpleNamespace(symbol="BTC/USDT", reserved_quote="100", state_json={}),
            SimpleNamespace(symbol="ETH/BTC", reserved_quote="0.01", state_json={}),
            SimpleNamespace(symbol="SOL/USDT", reserved_quote="25", state_json={}),
        ]
        self.assertEqual(reserved_capital_by_quote(deals), {"USDT": D("125"), "BTC": D("0.01")})
        with self.assertRaises(ValueError):
            reserved_capital_quote(deals)

    def test_risk_blocks_when_insufficient_quote_balance(self):
        limits = RiskLimits(max_capital=D("500"))
        snapshot = RiskSnapshot(D("1000"), D("49"), D("0"), D("0"), 0, False)
        with self.assertRaisesRegex(RuntimeError, "Insufficient available"):
            check_risk(limits, snapshot, D("50"))


if __name__ == "__main__":
    unittest.main()
