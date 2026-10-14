from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestnetDealRecord(Base):
    __tablename__ = "testnet_deals"
    __table_args__ = (UniqueConstraint("strategy_id", "client_deal_id", name="uq_testnet_deal_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    client_deal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default="active")
    base_qty: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    quote_cost: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    average_entry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    realized_pnl: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    dca_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
