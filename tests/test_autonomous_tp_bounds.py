from dca_bot.autonomous import analyze_market
from dca_bot.strategy import StrategyConfig
import time


def _config(**overrides):
    values = {
        "symbols": ["BTC/USDT"], "ema_fast": 10, "ema_slow": 30,
        "rsi_period": 14, "atr_period": 14, "max_safety_orders": 4,
        "base_order_quote": 10, "safety_order_quote": 10,
        "max_capital_per_deal": 100, "max_total_allocation": 120,
        "max_active_deals": 1, "take_profit_pct": 1.0, "min_net_profit_pct": 0.3,
    }
    values.update(overrides)
    return StrategyConfig(**values)


def _candles(closes):
    start = int(time.time() * 1000) - len(closes) * 60_000
    return [[start + i * 60_000, x * .999, x * 1.01, x * .99, x, 100.0] for i, x in enumerate(map(float, closes))]


def test_adaptive_tp_does_not_inherit_an_excessive_manual_target():
    result = analyze_market("BTCUSDT", "15m", _candles([200 - i * .7 for i in range(100)]), _config(take_profit_pct=8.0))
    assert result.parameters["take_profit_pct"] <= 3.0


def test_adaptive_tp_stays_high_enough_for_net_profit_floor():
    cfg = _config(take_profit_pct=8.0, min_net_profit_pct=0.3)
    result = analyze_market("BTCUSDT", "15m", _candles([200 - i * .7 for i in range(100)]), cfg)
    minimum = cfg.min_net_profit_pct + 2 * cfg.fee_rate_pct_per_side + 2 * cfg.slippage_pct_per_side
    assert result.parameters["take_profit_pct"] >= minimum
