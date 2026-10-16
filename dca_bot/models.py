from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Environment(StrEnum):
    TESTNET = "testnet"
    LIVE = "live"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    LIMIT_MAKER = "limit_maker"


class OrderStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GridConfig:
    symbol: str
    grid_type: str
    lower_price: Decimal
    upper_price: Decimal
    grids: int
    capital: Decimal
    grid_step_pct: Decimal | None = None
    max_open_orders: int = 20
    max_drawdown_pct: Decimal = Decimal("5")
    max_daily_loss_pct: Decimal = Decimal("2")
    range_break_buffer_pct: Decimal = Decimal("1")


@dataclass(frozen=True)
class OrderRequest:
    environment: Environment
    symbol: str
    side: OrderSide
    order_type: OrderType
    amount: Decimal
    price: Decimal | None = None
    client_order_id: str | None = None
    post_only: bool = False


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: OrderSide
    price: Decimal
    amount: Decimal
    quote_amount: Decimal
    fee: Decimal
    fee_currency: str
    order_id: str
    timestamp_ms: int
