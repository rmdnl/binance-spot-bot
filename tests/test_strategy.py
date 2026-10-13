from decimal import Decimal
import pytest
from dca_bot.strategy import StrategyConfig
from dca_bot.risk import estimate_capital, estimated_net_profit_pct, validate_budget


def test_strategy_defaults_to_disabled_spot_preset():
    config = StrategyConfig()
    assert config.enabled is False
    assert config.symbols == ['BTC/USDT']
    assert config.max_safety_orders == 4


def test_symbol_normalization():
    config = StrategyConfig(symbols=['btcusdt', 'ETH/USDT'])
    assert config.symbols == ['BTC/USDT', 'ETH/USDT']


def test_duplicate_symbols_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        StrategyConfig(symbols=['BTCUSDT', 'BTC/USDT'])


def test_unsafe_ema_order_rejected():
    with pytest.raises(ValueError, match='EMA fast'):
        StrategyConfig(ema_fast=200, ema_slow=50)


def test_estimated_capital_geometric_safety_orders():
    result = estimate_capital(Decimal('10'), Decimal('10'), 4, Decimal('1.5'))
    assert result.safety_orders == Decimal('81.250')
    assert result.total == Decimal('91.250')


def test_zero_safety_orders():
    result = estimate_capital(Decimal('10'), Decimal('10'), 0, Decimal('1.5'))
    assert result.total == Decimal('10')


def test_net_profit_estimate_accounts_for_both_sides():
    result = estimated_net_profit_pct(Decimal('1'), Decimal('0.1'), Decimal('0.05'))
    assert result == Decimal('0.70')


def test_budget_rejects_undersized_deal_cap():
    with pytest.raises(ValueError, match='exceeds per-deal cap'):
        validate_budget(Decimal('100'), Decimal('90'), Decimal('300'), 2)


def test_budget_rejects_total_overallocation():
    with pytest.raises(ValueError, match='total allocation'):
        validate_budget(Decimal('100'), Decimal('100'), Decimal('150'), 2)
