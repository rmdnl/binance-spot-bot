from spot_bot.exchange import BinanceSpot


class FakeExchange:
    def __init__(self):
        self.calls = []

    def fetch_my_trades(self, symbol, limit=None, params=None):
        self.calls.append((symbol, limit, params))
        return [{"id": "trade-1", "order": 123456789}]


def test_trades_for_order_passes_binance_order_id_filter():
    adapter = object.__new__(BinanceSpot)
    adapter.exchange = FakeExchange()

    trades = adapter.trades_for_order("BTC/USDT", "123456789")

    assert trades == [{"id": "trade-1", "order": 123456789}]
    assert adapter.exchange.calls == [
        ("BTC/USDT", 1000, {"orderId": 123456789}),
    ]


def test_trades_for_order_uses_recent_trades_for_non_numeric_id():
    adapter = object.__new__(BinanceSpot)
    adapter.exchange = FakeExchange()
    adapter.recent_trades = lambda symbol, limit=50: [
        {"id": "fallback-trade", "order": "legacy-id", "symbol": symbol, "limit": limit}
    ]

    trades = adapter.trades_for_order("BTC/USDT", "legacy-id")

    assert trades == [
        {"id": "fallback-trade", "order": "legacy-id", "symbol": "BTC/USDT", "limit": 100}
    ]
    assert adapter.exchange.calls == []
