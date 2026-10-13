from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class StrategyConfig(BaseModel):
    """Editable DCA parameters with conservative bounds and cross-field validation."""
    name: str = Field(default='My DCA strategy', min_length=1, max_length=80)
    enabled: bool = False
    symbols: list[str] = Field(default_factory=lambda: ['BTC/USDT'], min_length=1, max_length=30)
    quote_asset: str = 'USDT'
    order_size_mode: Literal['fixed_quote', 'balance_percent'] = 'fixed_quote'
    base_order_quote: float = Field(default=10.0, gt=0, le=1_000_000)
    base_order_balance_pct: float = Field(default=1.0, gt=0, le=100)
    safety_order_quote: float = Field(default=10.0, gt=0, le=1_000_000)
    max_safety_orders: int = Field(default=4, ge=0, le=30)
    safety_order_volume_scale: float = Field(default=1.5, ge=1.0, le=10.0)
    initial_deviation_pct: float = Field(default=1.0, gt=0, le=50)
    deviation_scale: float = Field(default=1.5, ge=1.0, le=10.0)
    take_profit_pct: float = Field(default=1.0, gt=0, le=100)
    take_profit_basis: Literal['average_entry', 'base_order'] = 'average_entry'
    dca_mode: Literal['position_averaging', 'order_averaging'] = 'position_averaging'
    averaging_method: Literal['fixed_order_value', 'fixed_coin_amount'] = 'fixed_order_value'
    safety_order_base_quantity: float = Field(default=0.001, gt=0, le=1_000_000)
    stop_loss_basis: Literal['average_entry', 'base_order'] = 'base_order'
    move_stop_to_breakeven_enabled: bool = False
    breakeven_activation_pct: float = Field(default=1.0, gt=0, le=100)
    breakeven_offset_pct: float = Field(default=0.0, ge=-10, le=10)
    reinvest_profit: bool = False
    max_deal_duration_hours: int = Field(default=0, ge=0, le=8760)
    start_condition: Literal['manual', 'immediately', 'ema_trend', 'rsi_oversold', 'ema_rsi', 'webhook'] = 'manual'
    trailing_take_profit_enabled: bool = False
    trailing_activation_pct: float = Field(default=1.0, ge=0, le=100)
    trailing_callback_pct: float = Field(default=0.3, gt=0, le=50)
    stop_loss_enabled: bool = False
    stop_loss_pct: float = Field(default=10.0, gt=0, le=99)
    max_active_deals: int = Field(default=3, ge=1, le=100)
    max_active_deals_per_symbol: int = Field(default=1, ge=1, le=20)
    cooldown_seconds: int = Field(default=300, ge=0, le=31_536_000)
    max_capital_per_deal: float = Field(default=200.0, gt=0, le=100_000_000)
    max_total_allocation: float = Field(default=600.0, gt=0, le=100_000_000)
    reserve_balance: float = Field(default=20.0, ge=0, le=100_000_000)
    daily_loss_limit_quote: float = Field(default=25.0, gt=0, le=100_000_000)
    max_equity_drawdown_pct: float = Field(default=5.0, gt=0, le=100)
    min_net_profit_pct: float = Field(default=0.3, ge=0, le=100)
    fee_rate_pct_per_side: float = Field(default=0.1, ge=0, le=5)
    slippage_pct_per_side: float = Field(default=0.05, ge=0, le=5)
    order_type: Literal['market', 'limit'] = 'limit'
    order_timeout_seconds: int = Field(default=60, ge=1, le=86400)
    timeframe: Literal['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d'] = '1h'
    entry_filter: Literal['manual', 'none', 'ema_trend', 'rsi', 'ema_rsi'] = 'manual'
    ema_fast: int = Field(default=50, ge=2, le=500)
    ema_slow: int = Field(default=200, ge=3, le=1000)
    rsi_period: int = Field(default=14, ge=2, le=100)
    rsi_max_entry: float = Field(default=40.0, ge=1, le=99)
    adx_min: float = Field(default=20.0, ge=0, le=100)
    atr_period: int = Field(default=14, ge=2, le=100)
    retry_limit: int = Field(default=3, ge=0, le=20)
    reconciliation_seconds: int = Field(default=30, ge=5, le=3600)

    @field_validator('symbols')
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            symbol = value.strip().upper().replace('-', '/')
            if not symbol or len(symbol) > 30 or any(ch.isspace() for ch in symbol):
                raise ValueError(f'Invalid trading symbol: {value!r}')
            if '/' not in symbol:
                if symbol.endswith('USDT') and len(symbol) > 4:
                    symbol = f'{symbol[:-4]}/USDT'
                else:
                    raise ValueError('Use BASE/QUOTE format or a supported pair such as BTCUSDT')
            if symbol in normalized:
                raise ValueError(f'Duplicate trading symbol: {symbol}')
            normalized.append(symbol)
        return normalized

    @model_validator(mode='after')
    def validate_risk(self) -> 'StrategyConfig':
        if self.ema_fast >= self.ema_slow:
            raise ValueError('EMA fast period must be less than EMA slow period')
        if self.max_capital_per_deal < self.base_order_quote:
            raise ValueError('Max capital per deal must cover the base order')
        if self.max_total_allocation < self.max_capital_per_deal:
            raise ValueError('Total allocation must be at least the per-deal cap')
        if self.trailing_take_profit_enabled and self.trailing_activation_pct < self.take_profit_pct:
            raise ValueError('Trailing activation must be at least the take-profit target')
        return self

    def estimated_max_deal_capital(self) -> float:
        """Quote amount for all planned orders before filters, fees, and slippage."""
        total = self.base_order_quote
        for index in range(self.max_safety_orders):
            total += self.safety_order_quote * (self.safety_order_volume_scale ** index)
        return round(total, 8)

    def estimated_total_capacity(self) -> float:
        return round(self.estimated_max_deal_capital() * self.max_active_deals, 8)

    def estimated_net_tp_pct(self) -> float:
        """Rough estimate only; actual PnL uses confirmed fills and actual commission."""
        return round(self.take_profit_pct - 2 * self.fee_rate_pct_per_side - 2 * self.slippage_pct_per_side, 6)
