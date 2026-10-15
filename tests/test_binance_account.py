from decimal import Decimal as D

from dca_bot.binance_account import BinanceAccountData, PnlRow


def test_pnl_is_aggregated_only_within_same_quote_currency():
    rows = [
        PnlRow("BTC/USDT", "USDT", "BTC", D("10"), D("2"), D("0"), D("0"), D("0")),
        PnlRow("ETH/USDT", "USDT", "ETH", D("3"), D("-1"), D("0"), D("0"), D("0")),
        PnlRow("ETH/BTC", "BTC", "ETH", D("4"), D("5"), D("0"), D("0"), D("0")),
        PnlRow("BTC/USDC", "USDC", "BTC", D("7"), D("1"), D("0"), D("0"), D("0")),
    ]

    summaries = BinanceAccountData.summarize_pnl_by_quote(rows)

    assert [(x.quote, x.realized, x.unrealized, x.total) for x in summaries] == [
        ("BTC", D("4"), D("5"), D("9")),
        ("USDC", D("7"), D("1"), D("8")),
        ("USDT", D("13"), D("1"), D("14")),
    ]


def test_pnl_summary_does_not_create_cross_currency_total():
    rows = [
        PnlRow("BTC/USDT", "USDT", "BTC", D("10"), D("2"), D("0"), D("0"), D("0")),
        PnlRow("ETH/BTC", "BTC", "ETH", D("4"), D("5"), D("0"), D("0"), D("0")),
    ]

    summaries = BinanceAccountData.summarize_pnl_by_quote(rows)

    assert [x.quote for x in summaries] == ["BTC", "USDT"]
    assert all(x.quote in {"BTC", "USDT"} for x in summaries)
    assert sum((x.total for x in summaries), D("0")) == D("21")
