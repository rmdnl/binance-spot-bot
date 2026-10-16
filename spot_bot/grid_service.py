from decimal import Decimal

from .models import GridConfig
from .grid import build_grid

D = Decimal


def validate_grid_config(config: GridConfig) -> None:
    if not config.symbol or "/" not in config.symbol:
        raise ValueError("symbol must use BASE/QUOTE format")
    if config.capital <= 0:
        raise ValueError("capital must be positive")
    if config.max_open_orders < 1:
        raise ValueError("max_open_orders must be positive")
    preview = build_grid(config.lower_price, config.upper_price, config.grids, config.grid_type)
    if config.stop_loss_price is not None:
        if config.stop_loss_price <= 0 or config.stop_loss_price >= preview.lower_price:
            raise ValueError("stop loss must be below lower grid price")


def initial_grid_orders(config: GridConfig):
    validate_grid_config(config)
    preview = build_grid(config.lower_price, config.upper_price, config.grids, config.grid_type)
    # Order creation is intentionally separated from exchange submission.
    # The caller can validate balances and Binance filters before sending anything.
    return preview
