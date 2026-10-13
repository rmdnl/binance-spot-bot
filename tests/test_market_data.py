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
        "ETH3L/USDT": {"spot": True, "active": True, "base": "ETH3L", "quote": "USDT"},
        "GROUP/USDT": {"spot": True, "active": True, "base": "GROUP", "quote": "USDT"},
    }
    tickers = {
        "BTC/USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 20_000_000, "timestamp": now_ms},
        "ETH/USDT": {"last": 50, "bid": 49.99, "ask": 50.01, "quoteVolume": 10_000_000, "timestamp": now_ms},
        "SOL/USDT": {"last": 20, "bid": 19, "ask": 21, "quoteVolume": 30_000_000, "timestamp": now_ms},
        "BTC/FDUSD": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "BTC/USDT:USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "BNB/USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "ETHUP/USDT": {"last": 1, "bid": 0.99, "ask": 1.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "ETH3L/USDT": {"last": 1, "bid": 0.99, "ask": 1.01, "quoteVolume": 99_000_000, "timestamp": now_ms},
        "GROUP/USDT": {"last": 10, "bid": 9.99, "ask": 10.01, "quoteVolume": 8_000_000, "timestamp": now_ms},
    }

    class DiscoveryExchange(FakeExchange):
        def fetch_tickers(self):
            return tickers

    exchange = DiscoveryExchange(markets, {})
    result = BinanceSpotMarketData(exchange).discover_usdt_symbols(
        min_quote_volume_24h=5_000_000, max_spread_pct=0.25, limit=10
    )
    assert result == ["BTC/USDT", "ETH/USDT", "GROUP/USDT"]


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



@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_quote_volume_24h": float("nan")},
        {"min_quote_volume_24h": float("inf")},
        {"max_spread_pct": float("nan")},
        {"max_spread_pct": float("inf")},
    ],
)
def test_discovery_rejects_non_finite_thresholds(kwargs):
    class DiscoveryExchange(FakeExchange):
        def fetch_tickers(self):
            return {}
    with pytest.raises(ValueError, match="Invalid automatic pair discovery limits"):
        BinanceSpotMarketData(DiscoveryExchange({}, {})).discover_usdt_symbols(**kwargs)


def test_discovery_reuses_bulk_ticker_snapshot_for_multiple_profiles():
    now_ms = int(time.time() * 1000)
    markets = {
        "BTC/USDT": {"spot": True, "active": True, "base": "BTC", "quote": "USDT"},
        "ETH/USDT": {"spot": True, "active": True, "base": "ETH", "quote": "USDT"},
    }
    class DiscoveryExchange(FakeExchange):
        ticker_calls = 0

        def fetch_tickers(self):
            self.ticker_calls += 1
            return {
                "BTC/USDT": {"last": 100, "bid": 99.99, "ask": 100.01, "quoteVolume": 20_000_000, "timestamp": now_ms},
                "ETH/USDT": {"last": 50, "bid": 49.99, "ask": 50.01, "quoteVolume": 10_000_000, "timestamp": now_ms},
            }

    exchange = DiscoveryExchange(markets, {})
    adapter = BinanceSpotMarketData(exchange)
    assert adapter.discover_usdt_symbols(limit=1) == ["BTC/USDT"]
    assert adapter.discover_usdt_symbols(limit=2) == ["BTC/USDT", "ETH/USDT"]
    assert exchange.ticker_calls == 1



def test_minimum_order_validation_rejects_base_order_below_market_min_cost():
    from dca_bot.strategy import StrategyConfig

    markets = {
        "BTC/USDT": {
            "spot": True,
            "active": True,
            "info": {"filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000000", "tickSize": "0.01"},
            ]},
            "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
        }
    }
    adapter = BinanceSpotMarketData(FakeExchange(markets, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=1,
        safety_order_quote=1,
        max_safety_orders=0,
        max_capital_per_deal=10,
        max_total_allocation=10,
    )
    with pytest.raises(ValueError, match="below the market minimum cost"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("50000"))


def test_minimum_order_validation_rejects_quantity_below_market_min_amount():
    from dca_bot.strategy import StrategyConfig

    markets = {
        "BTC/USDT": {
            "spot": True,
            "active": True,
            "info": {"filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000000", "tickSize": "0.01"},
            ]},
            "limits": {"amount": {"min": 0.01}, "cost": {"min": 0}},
        }
    }
    adapter = BinanceSpotMarketData(FakeExchange(markets, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=0,
        max_capital_per_deal=20,
        max_total_allocation=20,
    )
    with pytest.raises(ValueError, match="below the market minimum amount"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("50000"))




def test_minimum_order_validation_fails_closed_when_market_active_flag_is_missing():
    from dca_bot.strategy import StrategyConfig

    adapter = BinanceSpotMarketData(FakeExchange({
        "BTC/USDT": {
            "spot": True,
            "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
            "info": {"filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000000", "tickSize": "0.01"},
            ]},
        }
    }, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=0,
        max_capital_per_deal=20,
        max_total_allocation=20,
    )
    with pytest.raises(ValueError, match="not an active Binance Spot market"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("50000"))


def test_minimum_order_validation_fails_closed_when_market_minimums_are_missing():
    from dca_bot.strategy import StrategyConfig

    adapter = BinanceSpotMarketData(FakeExchange(
        {"BTC/USDT": {"spot": True, "active": True}}, {}
    ))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=0,
        max_capital_per_deal=20,
        max_total_allocation=20,
    )
    with pytest.raises(ValueError, match="minimum-order metadata is unavailable"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("50000"))


def test_minimum_order_validation_blocks_when_binance_precision_filters_are_missing():
    from dca_bot.strategy import StrategyConfig

    adapter = BinanceSpotMarketData(FakeExchange({
        "BTC/USDT": {
            "spot": True,
            "active": True,
            "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
        }
    }, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=0,
        max_capital_per_deal=20,
        max_total_allocation=20,
    )
    with pytest.raises(ValueError, match="LOT_SIZE/PRICE_FILTER metadata is unavailable"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("50000"))


def test_minimum_order_validation_uses_stricter_binance_notional_filter():
    from dca_bot.strategy import StrategyConfig

    markets = {
        "BTC/USDT": {
            "spot": True,
            "active": True,
            "info": {"filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000000", "tickSize": "0.01"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "10", "applyToMarket": True, "avgPriceMins": 5},
            ]},
            "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
        }
    }
    adapter = BinanceSpotMarketData(FakeExchange(markets, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=8,
        safety_order_quote=1,
        max_safety_orders=0,
        max_capital_per_deal=10,
        max_total_allocation=10,
    )
    with pytest.raises(ValueError, match="below the market minimum cost 10"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("100"))


def test_minimum_order_validation_rejects_order_above_binance_max_notional():
    from dca_bot.strategy import StrategyConfig

    markets = {
        "BTC/USDT": {
            "spot": True,
            "active": True,
            "info": {"filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000000", "tickSize": "0.01"},
                {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "10", "applyMinToMarket": True, "applyMaxToMarket": True, "avgPriceMins": 5},
            ]},
            "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
        }
    }
    adapter = BinanceSpotMarketData(FakeExchange(markets, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=20,
        safety_order_quote=1,
        max_safety_orders=0,
        max_capital_per_deal=25,
        max_total_allocation=25,
    )
    with pytest.raises(ValueError, match="exceeds Binance maximum notional 10"):
        adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("100"))


def test_minimum_order_validation_checks_minimum_cost_after_step_rounding():
    from dca_bot.strategy import StrategyConfig

    markets = {
        "BTC/USDT": {
            "spot": True,
            "active": True,
            "info": {"filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "10000000", "tickSize": "0.01"},
            ]},
            "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
        }
    }
    adapter = BinanceSpotMarketData(FakeExchange(markets, {}))
    config = StrategyConfig(
        symbols=["BTC/USDT"],
        base_order_quote=5.01,
        safety_order_quote=5.01,
        max_safety_orders=0,
        max_capital_per_deal=10,
        max_total_allocation=10,
    )
    # At 50000 USDT, the base quantity rounds down from 0.0001002 to 0.0001,
    # leaving a rounded notional of exactly 5 USDT, so it remains eligible.
    adapter.validate_minimum_order_sizes("BTC/USDT", config, Decimal("50000"))
