from decimal import Decimal

from dca_bot.risk import estimate_capital, estimated_net_profit_pct, validate_budget
from dca_bot.strategy import StrategyConfig


def test_config_defaults_to_manual_spot_dca():
    config = StrategyConfig()
    assert config.enabled is False
    assert config.symbols == ['BTC/USDT']
    assert config.pair_selection_mode == 'manual'
    assert config.quote_asset == 'USDT'
    assert config.max_safety_orders == 4


def test_symbol_normalization():
    assert StrategyConfig(symbols=['btcusdt', 'ETH/USDT']).symbols == ['BTC/USDT', 'ETH/USDT']


def test_duplicate_symbols_rejected():
    try:
        StrategyConfig(symbols=['BTCUSDT', 'BTC/USDT'])
    except ValueError as exc:
        assert 'Duplicate' in str(exc)
    else:
        raise AssertionError('Duplicate symbol was accepted')


def test_estimated_capital_geometric_safety_orders():
    result = estimate_capital(Decimal('10'), Decimal('10'), 4, Decimal('1.5'))
    assert result.safety_orders == Decimal('81.250')
    assert result.total == Decimal('91.250')


def test_zero_safety_orders():
    assert estimate_capital(Decimal('10'), Decimal('10'), 0, Decimal('1.5')).total == Decimal('10')


def test_net_profit_estimate_accounts_for_both_sides():
    assert estimated_net_profit_pct(Decimal('1'), Decimal('0.1'), Decimal('0.05')) == Decimal('0.70')


def test_budget_rejects_undersized_deal_cap():
    try:
        validate_budget(Decimal('100'), Decimal('90'), Decimal('300'), 2)
    except ValueError as exc:
        assert 'exceeds per-deal cap' in str(exc)
    else:
        raise AssertionError('Undersized deal cap was accepted')


def test_budget_rejects_total_overallocation():
    try:
        validate_budget(Decimal('100'), Decimal('100'), Decimal('150'), 2)
    except ValueError as exc:
        assert 'total allocation' in str(exc)
    else:
        raise AssertionError('Overallocated configuration was accepted')


def test_fixed_coin_capital_requires_reference_price():
    config = StrategyConfig(averaging_method='fixed_coin_amount', safety_order_base_quantity=0.01)
    try:
        config.estimated_max_deal_capital()
    except ValueError as exc:
        assert 'reference price' in str(exc)
    else:
        raise AssertionError('Fixed coin estimate without a price should be rejected')


def test_fixed_coin_capital_uses_dca_price_ladder():
    config = StrategyConfig(
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=2,
        initial_deviation_pct=1,
        deviation_scale=1,
        averaging_method='fixed_coin_amount',
        safety_order_base_quantity=0.1,
        safety_order_volume_scale=1,
    )
    estimated = config.estimated_max_deal_capital(reference_price=100)
    assert estimated == 10 + 0.1 * 99 + 0.1 * 98


def test_optional_trigger_and_price_range_validate_together():
    config = StrategyConfig(
        trigger_price=100,
        price_range_enabled=True,
        price_range_min=90,
        price_range_max=110,
    )
    assert config.trigger_price == 100


def test_take_profit_net_profit_guard_math():
    config = StrategyConfig(
        take_profit_pct=1,
        min_net_profit_pct=0.3,
        fee_rate_pct_per_side=0.1,
        slippage_pct_per_side=0.05,
    )
    assert config.estimated_net_tp_pct() == 0.7
