from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

D = Decimal


class Base(DeclarativeBase):
    pass


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
    NEW = "new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class GridStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"


def now_utc():
    return datetime.now(UTC)


@dataclass(frozen=True)
class OrderRequest:
    environment: Environment
    symbol: str
    side: OrderSide
    order_type: OrderType
    amount: D
    price: D | None = None
    client_order_id: str | None = None
    post_only: bool = False


@dataclass(frozen=True)
class GridConfig:
    symbol: str
    lower_price: D
    upper_price: D
    grids: int
    capital: D
    grid_type: str = "arithmetic"
    max_open_orders: int = 20
    stop_loss_price: D | None = None
    stop_loss_enabled: bool = False


class SpotOrder(Base):
    __tablename__ = "spot_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default=OrderStatus.NEW.value, index=True)
    amount: Mapped[D] = mapped_column(Numeric(38, 18))
    price: Mapped[D | None] = mapped_column(Numeric(38, 18), nullable=True)
    filled_amount: Mapped[D] = mapped_column(Numeric(38, 18), default=D("0"))
    average_price: Mapped[D | None] = mapped_column(Numeric(38, 18), nullable=True)
    fee_quote: Mapped[D] = mapped_column(Numeric(38, 18), default=D("0"))
    client_order_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    fills: Mapped[list["SpotFill"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class SpotFill(Base):
    __tablename__ = "spot_fills"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("spot_orders.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(4))
    price: Mapped[D] = mapped_column(Numeric(38, 18))
    amount: Mapped[D] = mapped_column(Numeric(38, 18))
    quote_amount: Mapped[D] = mapped_column(Numeric(38, 18))
    fee: Mapped[D] = mapped_column(Numeric(38, 18), default=D("0"))
    fee_currency: Mapped[str] = mapped_column(String(20), default="")
    exchange_trade_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    order: Mapped[SpotOrder] = relationship(back_populates="fills")


class InventoryPosition(Base):
    __tablename__ = "inventory_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    base_asset: Mapped[str] = mapped_column(String(20))
    quote_asset: Mapped[str] = mapped_column(String(20))
    base_quantity: Mapped[D] = mapped_column(Numeric(38, 18), default=D("0"))
    quote_cost: Mapped[D] = mapped_column(Numeric(38, 18), default=D("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    __table_args__ = (
        UniqueConstraint("environment", "symbol", name="uq_inventory_environment_symbol"),
    )


class GridBot(Base):
    __tablename__ = "grid_bots"
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    grid_type: Mapped[str] = mapped_column(String(12))
    lower_price: Mapped[D] = mapped_column(Numeric(38, 18))
    upper_price: Mapped[D] = mapped_column(Numeric(38, 18))
    grids: Mapped[int] = mapped_column(Integer)
    capital_quote: Mapped[D] = mapped_column(Numeric(38, 18))
    status: Mapped[str] = mapped_column(String(16), default=GridStatus.DRAFT.value, index=True)
    max_open_orders: Mapped[int] = mapped_column(Integer, default=20)
    stop_loss_price: Mapped[D | None] = mapped_column(Numeric(38, 18), nullable=True)
    stop_loss_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    levels: Mapped[list["GridLevel"]] = relationship(back_populates="bot", cascade="all, delete-orphan")


class GridLevel(Base):
    __tablename__ = "grid_levels"
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("grid_bots.id"), index=True)
    level_index: Mapped[int] = mapped_column(Integer)
    price: Mapped[D] = mapped_column(Numeric(38, 18))
    buy_order_id: Mapped[int | None] = mapped_column(ForeignKey("spot_orders.id"), nullable=True)
    sell_order_id: Mapped[int | None] = mapped_column(ForeignKey("spot_orders.id"), nullable=True)
    bot: Mapped[GridBot] = relationship(back_populates="levels")
    __table_args__ = (UniqueConstraint("bot_id", "level_index", name="uq_grid_level"),)


class RiskState(Base):
    __tablename__ = "risk_state"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
