from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Index
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
    """Durable DCA deal header plus authoritative lifecycle/accounting fields.

    ``state_json`` is retained as a compatibility snapshot while the structured
    columns become the source of truth for lifecycle and accounting reads.
    """
    __tablename__ = "paper_deals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    quote_asset: Mapped[str] = mapped_column(String(20), index=True, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="active")

    base_price: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    last_price: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    quote_spent: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    base_quantity: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    entry_fees_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    exit_price: Mapped[float | None] = mapped_column(Numeric(38, 18), nullable=True)
    realized_pnl_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    close_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class DealOrderRecord(Base):
    """Durable order/fill ledger for one DCA deal.

    Paper mode records simulated orders here. The schema is deliberately
    exchange-neutral so a future execution adapter can use the same lifecycle
    contract without reintroducing autonomous entry selection.
    """
    __tablename__ = "deal_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[str] = mapped_column(ForeignKey("paper_deals.id"), index=True, nullable=False)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    order_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    base_quantity: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    quote_amount: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    fee_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    client_order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("deal_id", "sequence", name="uq_deal_orders_deal_sequence"),
        Index("ix_deal_orders_deal_status", "deal_id", "status"),
    )


class DealEventRecord(Base):
    """Append-only lifecycle ledger for a DCA deal."""
    __tablename__ = "deal_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[str] = mapped_column(ForeignKey("paper_deals.id"), index=True, nullable=False)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False)
    quote_amount: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    base_quantity: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    pnl_quote: Mapped[float | None] = mapped_column(Numeric(38, 18), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_deal_events_deal_created", "deal_id", "created_at"),
    )


class DealAccountingRecord(Base):
    """Quote-aware accounting snapshot for one deal."""
    __tablename__ = "deal_accounting"
    deal_id: Mapped[str] = mapped_column(ForeignKey("paper_deals.id"), primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    base_asset: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    quote_invested: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    quote_proceeds: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    entry_fees_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    exit_fees_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    slippage_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    realized_pnl_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    unrealized_pnl_quote: Mapped[float] = mapped_column(Numeric(38, 18), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class StrategyRiskState(Base):
    """Persistent paper-equity reference and latched drawdown circuit breaker."""
    __tablename__ = "strategy_risk_states"
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), primary_key=True)
    reference_equity_quote: Mapped[str] = mapped_column(String(64), nullable=False)
    peak_equity_quote: Mapped[str] = mapped_column(String(64), nullable=False)
    drawdown_tripped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    drawdown_trip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
