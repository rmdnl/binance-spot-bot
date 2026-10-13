from decimal import Decimal
import time

import pytest

from dca_bot.market_data import BinanceSpotMarketData


class FakeExchange:
    def __init__(self, markets, ticker):
        self.markets = markets
        self.ticker_data = ticker
        self.requested_symbol = None

    def load_markets(self):
        return self.markets

    def fetch_ticker(self, symbol):
        self.requested_symbol = symbol
        return self.ticker_data


def test_market_data_normalizes_symbol_and_returns_spot_ticker():
    exchange = FakeExchange(
        {"BTC/USDT": {"spot": True, "active": True}},
        {"last": 123.45, "bid": 123.4, "ask": 123.5, "timestamp": int(time.time() * 1000)},
    )
    ticker = BinanceSpotMarketData(exchange).ticker("btcusdt")
    assert ticker.symbol == "BTC/USDT"
    assert ticker.last == Decimal("123.45")
    assert ticker.bid == Decimal("123.4")
    assert ticker.ask == Decimal("123.5")
    assert exchange.requested_symbol == "BTC/USDT"


def test_market_data_rejects_non_spot_market():
    exchange = FakeExchange({"BTC/USDT": {"spot": False, "active": True}}, {"last": 10})
    with pytest.raises(ValueError, match="active Binance Spot"):
        BinanceSpotMarketData(exchange).ticker("BTC/USDT")


def test_market_data_rejects_unknown_market():
    with pytest.raises(ValueError, match="was not found"):
        BinanceSpotMarketData(FakeExchange({}, {})).ticker("BTC/USDT")


def test_market_data_rejects_invalid_price():
    exchange = FakeExchange({"BTC/USDT": {"spot": True, "active": True}}, {"last": 0})
    with pytest.raises(ValueError, match="non-positive"):
        BinanceSpotMarketData(exchange).ticker("BTC/USDT")


@pytest.mark.parametrize(
    "timestamp, message",
    [
        (lambda: int((time.time() - 180) * 1000), "stale"),
        (lambda: int((time.time() + 120) * 1000), "future"),
        (lambda: None, "missing or invalid"),
    ],
)
def test_market_data_rejects_stale_future_or_missing_ticker_timestamp(timestamp, message):
    value = timestamp()
    exchange = FakeExchange(
        {"BTC/USDT": {"spot": True, "active": True}},
        {"last": 100, "timestamp": value},
    )
    with pytest.raises(ValueError, match=message):
        BinanceSpotMarketData(exchange).ticker("BTC/USDT")


def test_market_data_accepts_fresh_ticker_timestamp():
    now_ms = int(time.time() * 1000)
    exchange = FakeExchange(
        {"BTC/USDT": {"spot": True, "active": True}},
        {"last": 100, "timestamp": now_ms},
    )
    ticker = BinanceSpotMarketData(exchange).ticker("BTC/USDT")
    assert ticker.timestamp_ms == now_ms



def test_discovery_returns_only_liquid_fresh_active_usdt_spot_pairs_ranked_by_volume():
    now_ms = int(time.time() * 1000)
    markets = {
        "BTC/USDT": {"spot": True, "active": True, "base": "BTC", "quote": "USDT"},
        "ETH/USDT": {"spot": True, "active": True, "base": "ETH", "quote": "USDT"},
        "SOL/USDT": {"spot": True, "active": True, "base": "SOL", "quote": "USDT"},
        "BTC/FDUSD": {"spot": True, "active": True, "base": "BTC", "quote": "FDUSD"},
        "BTC/USDT:USDT": {"spot": False, "active": True, "base": "BTC", "quote": "USDT"},
        "BNB/USDT": {"spot": True, "active": False, "base": "BNB", "quote": "USDT"},
        "ETHUP/USDT": {"spot": True, "active": True, "base": "ETHUP", "quote": "USDT"},
    }
    tickers = {
        "BTC/USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 20_000_000, "timestamp": now_ms},
        "ETH/USDT": {"last": 50, "bid": 49.99, "ask": 50.01, "quoteVolume": 10_000_000, "timestamp": now_ms},
        "SOL/USDT": {"last": 20, "bid": 19, "ask": 21, "quoteVolume": 30_000_000, "timestamp": now_ms},
        "BTC/FDUSD": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "BTC/USDT:USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "BNB/USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "ETHUP/USDT": {"last": 1, "bid": 0.99, "ask": 1.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
    }

    class DiscoveryExchange(FakeExchange):
        def fetch_tickers(self):
            return tickers

    exchange = DiscoveryExchange(markets, {})
    result = BinanceSpotMarketData(exchange).discover_usdt_symbols(
        min_quote_volume_24h=5_000_000, max_spread_pct=0.25, limit=10
    )
    assert result == ["BTC/USDT", "ETH/USDT"]


def test_discovery_rejects_stale_or_missing_ticker_data():
    old = int((time.time() - 240) * 1000)
    markets = {
        "BTC/USDT": {"spot": True, "active": True, "base": "BTC", "quote": "USDT"},
        "ETH/USDT": {"spot": True, "active": True, "base": "ETH", "quote": "USDT"},
    }
    class DiscoveryExchange(FakeExchange):
        def fetch_tickers(self):
            return {
                "BTC/USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 20_000_000, "timestamp": old},
                "ETH/USDT": {"last": 50, "bid": 49.99, "ask": 50.01, "quoteVolume": 20_000_000},
            }
    assert BinanceSpotMarketData(DiscoveryExchange(markets, {})).discover_usdt_symbols() == []


def test_discovery_validates_limits():
    class DiscoveryExchange(FakeExchange):
        def fetch_tickers(self):
            return {}
    adapter = BinanceSpotMarketData(DiscoveryExchange({}, {}))
    with pytest.raises(ValueError, match="Invalid automatic pair discovery limits"):
        adapter.discover_usdt_symbols(limit=0)
