from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyRecord(Base):
    __tablename__ = 'strategies'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), default='admin', nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaperDealRecord(Base):
    __tablename__ = "paper_deals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="active")
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TestnetDealRecord(Base):
    __tablename__ = "testnet_deals"
    __table_args__ = (UniqueConstraint("client_deal_id", name="uq_testnet_deals_client_deal_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    client_deal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="active")
    base_qty: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    quote_cost: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    average_entry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    realized_pnl: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    dca_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ExchangeOrderRecord(Base):
    __tablename__ = "exchange_orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_exchange_orders_client_order_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("testnet_deals.id"), index=True, nullable=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="intent")
    amount: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[str] = mapped_column(String(64), nullable=False)
    filled: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    last_accounted_filled: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    remaining: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    average: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class StrategyRiskState(Base):
    __tablename__ = "strategy_risk_states"
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), primary_key=True)
    reference_equity_quote: Mapped[str] = mapped_column(String(64), nullable=False)
    peak_equity_quote: Mapped[str] = mapped_column(String(64), nullable=False)
    drawdown_tripped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    drawdown_trip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
