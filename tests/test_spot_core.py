from decimal import Decimal as D
from spot_bot.grid import build_grid, grid_profit_pct
from spot_bot.accounting import Position, unrealized_pnl, realized_pnl_from_sell
from spot_bot.risk import RiskLimits, RiskSnapshot, check_order_risk, quote_asset

def test_grid_example():
    g=build_grid(D('80000'),D('90000'),11,'arithmetic')
    assert len(g.levels)==11 and g.levels[0].price==D('80000') and g.levels[-1].price==D('90000')
    assert g.step_pct > D('1')

def test_quote_aware_accounting():
    p=Position('BTC/USDT','BTC','USDT',D('0.01'),D('800'))
    assert unrealized_pnl(p,D('90000'))==D('100')
    assert realized_pnl_from_sell(p,D('0.005'),D('90000'))==D('50')

def test_quote_asset_and_risk():
    assert quote_asset('ETH/BTC')=='BTC'
    s=RiskSnapshot(D('1000'),D('600'),D('0'),D('0'),0)
    check_order_risk(RiskLimits(D('500')),s,D('400'))
