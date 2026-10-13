from decimal import Decimal

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
