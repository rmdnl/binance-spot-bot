from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class StrategyConfig(BaseModel):
    """Single dashboard-owned DCA configuration.

    The class is retained as the internal persistence schema for compatibility;
    there are no selectable strategy profiles or autonomous pair discovery.
    """
    name: str = Field(default='DCA Configuration', min_length=1, max_length=80)
    enabled: bool = False
    symbols: list[str] = Field(default_factory=lambda: ['BTC/USDT'], min_length=1, max_length=30)
    pair_selection_mode: Literal['manual'] = 'manual'
    quote_asset: str = 'USDT'
    order_size_mode: Literal['fixed_quote', 'balance_percent'] = 'fixed_quote'
    base_order_quote: float = Field(default=10.0, gt=0, le=1_000_000)
    base_order_balance_pct: float = Field(default=1.0, gt=0, le=100)
    safety_order_quote: float = Field(default=10.0, gt=0, le=1_000_000)
    max_safety_orders: int = Field(default=4, ge=0, le=30)
    safety_order_volume_scale: float = Field(default=1.0, ge=1.0, le=10.0)
    initial_deviation_pct: float = Field(default=1.0, gt=0, le=50)
    deviation_scale: float = Field(default=1.0, ge=1.0, le=10.0)
    take_profit_pct: float = Field(default=1.0, gt=0, le=100)
    take_profit_basis: Literal['average_entry', 'base_order'] = 'average_entry'
    dca_mode: Literal['position_averaging', 'order_averaging'] = 'position_averaging'
    averaging_method: Literal['fixed_order_value', 'fixed_coin_amount'] = 'fixed_order_value'
    safety_order_base_quantity: float = Field(default=0.001, gt=0, le=1_000_000)

    # Optional advanced controls. None/disabled means the corresponding guard is off.
    trigger_price: float | None = Field(default=None, gt=0)
    price_range_enabled: bool = False
    price_range_min: float | None = Field(default=None, gt=0)
    price_range_max: float | None = Field(default=None, gt=0)
    cooldown_seconds: int = Field(default=0, ge=0, le=31_536_000)

    trailing_take_profit_enabled: bool = False
    trailing_activation_pct: float = Field(default=1.0, ge=0, le=100)
    trailing_callback_pct: float = Field(default=0.3, gt=0, le=50)
    stop_loss_enabled: bool = False
    stop_loss_pct: float = Field(default=10.0, gt=0, le=99)
    stop_loss_basis: Literal['average_entry', 'base_order'] = 'base_order'
    end_bot_on_stop_loss: bool = False
    move_stop_to_breakeven_enabled: bool = False
    breakeven_activation_pct: float = Field(default=1.0, gt=0, le=100)
    breakeven_offset_pct: float = Field(default=0.0, ge=-10, le=10)
    reinvest_profit: bool = False
    max_deal_duration_hours: int = Field(default=0, ge=0, le=8760)

    max_active_deals: int = Field(default=3, ge=1, le=100)
    max_active_deals_per_symbol: int = Field(default=1, ge=1, le=20)
    max_capital_per_deal: float = Field(default=200.0, gt=0, le=100_000_000)
    max_total_allocation: float = Field(default=600.0, gt=0, le=100_000_000)
    reserve_balance: float = Field(default=20.0, ge=0, le=100_000_000)
    daily_loss_limit_quote: float = Field(default=25.0, gt=0, le=100_000_000)
    max_equity_drawdown_pct: float = Field(default=5.0, gt=0, le=100)
    min_net_profit_pct: float = Field(default=0.3, ge=0, le=100)
    fee_rate_pct_per_side: float = Field(default=0.1, ge=0, le=5)
    slippage_pct_per_side: float = Field(default=0.05, ge=0, le=5)
    order_type: Literal['limit'] = 'limit'
    order_timeout_seconds: int = Field(default=60, ge=1, le=86400)
    reconciliation_seconds: int = Field(default=30, ge=5, le=3600)

    # Retained only so old persisted/test configuration can still deserialize.
    # These fields are not exposed as trading controls and are never used for entry selection.
    ema_fast: int = Field(default=50, ge=2, le=500)
    ema_slow: int = Field(default=200, ge=3, le=1000)
    rsi_period: int = Field(default=14, ge=2, le=100)
    rsi_max_entry: float = Field(default=40.0, ge=1, le=99)
    adx_min: float = Field(default=20.0, ge=0, le=100)
    atr_period: int = Field(default=14, ge=2, le=100)

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
            if not symbol.endswith('/USDT'):
                raise ValueError('Only USDT spot pairs are supported')
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
        if self.price_range_enabled:
            if self.price_range_min is None or self.price_range_max is None:
                raise ValueError('Both minimum and maximum price are required when Price Range is enabled')
            if self.price_range_min >= self.price_range_max:
                raise ValueError('Price Range minimum must be below maximum')
        if self.trigger_price is not None and self.price_range_enabled:
            if not self.price_range_min <= self.trigger_price <= self.price_range_max:
                raise ValueError('Trigger Price must be inside the configured Price Range')
        return self

    def estimated_max_deal_capital(self, reference_price: float | None = None) -> float:
        """Quote amount for all planned orders before fees and exchange filters."""
        if self.averaging_method == 'fixed_coin_amount':
            if reference_price is None or reference_price <= 0:
                raise ValueError('A positive reference price is required to estimate fixed-coin orders')
            from decimal import Decimal
            from .dca_math import build_dca_summary
            summary = build_dca_summary(
                base_price=Decimal(str(reference_price)),
                base_order_quote=Decimal(str(self.base_order_quote)),
                safety_order_quote=Decimal(str(self.safety_order_quote)),
                safety_order_count=self.max_safety_orders,
                initial_deviation_pct=Decimal(str(self.initial_deviation_pct)),
                deviation_scale=Decimal(str(self.deviation_scale)),
                safety_volume_scale=Decimal(str(self.safety_order_volume_scale)),
                averaging_method=self.averaging_method,
                safety_order_base_quantity=Decimal(str(self.safety_order_base_quantity)),
                dca_mode=self.dca_mode,
            )
            return float(summary.total_quote_required)
        total = self.base_order_quote
        for index in range(self.max_safety_orders):
            exponent = index if self.dca_mode == 'position_averaging' else index + 1
            total += self.safety_order_quote * (self.safety_order_volume_scale ** exponent)
        return round(total, 8)

    def estimated_total_capacity(self, reference_price: float | None = None) -> float:
        return round(self.estimated_max_deal_capital(reference_price) * self.max_active_deals, 8)

    def estimated_net_tp_pct(self) -> float:
        return round(self.take_profit_pct - 2 * self.fee_rate_pct_per_side - 2 * self.slippage_pct_per_side, 6)
