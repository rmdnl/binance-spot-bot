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


def test_historical_price_uses_latest_market_trade_not_after_fill_time():
    from datetime import UTC, datetime

    class HistoricalExchange(FakeExchange):
        def market(self, symbol):
            return {"spot": True}

        def fetch_trades(self, symbol, since=None, limit=None):
            self.historical_call = (symbol, since, limit)
            return [
                {"timestamp": since + 1000, "price": "2"},
                {"timestamp": since + 2000, "price": "3"},
                {"timestamp": since + 3000, "price": "999"},
            ]

    adapter = object.__new__(BinanceSpot)
    adapter.exchange = HistoricalExchange()
    fill_time = datetime.fromtimestamp((adapter.exchange and 1_800_000_000_000) / 1000, UTC)
    price = adapter.historical_price("BNB/USDT", fill_time, max_age_ms=5000)

    assert price == __import__("decimal").Decimal("3")
    assert adapter.exchange.historical_call[0] == "BNB/USDT"
