from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import ExchangeOrderRecord

D = Decimal


@dataclass(frozen=True)
class DcaOrderSpec:
    deal_id: int
    strategy_id: int
    symbol: str
    side: str
    amount: D
    price: D
    level: int


def client_order_id(deal_id: int, level: int) -> str:
    if deal_id <= 0 or level < 1:
        raise ValueError("invalid deal or DCA level")
    return f"dca-{deal_id}-{level}"


def get_or_create_dca_intent(session: Session, spec: DcaOrderSpec) -> ExchangeOrderRecord:
    cid = client_order_id(spec.deal_id, spec.level)
    existing = session.query(ExchangeOrderRecord).filter_by(client_order_id=cid).one_or_none()
    if existing:
        if existing.deal_id != spec.deal_id or existing.symbol != spec.symbol or existing.side != spec.side:
            raise RuntimeError("DCA client order id collision")
        return existing

    row = ExchangeOrderRecord(
        strategy_id=spec.strategy_id,
        deal_id=spec.deal_id,
        symbol=spec.symbol,
        side=spec.side,
        client_order_id=cid,
        status="intent",
        amount=str(spec.amount),
        price=str(spec.price),
        filled="0",
        remaining=str(spec.amount),
        raw_json="{}",
    )
    session.add(row)
    session.flush()
    return row


def next_dca_level(session: Session, deal_id: int) -> int:
    rows = session.query(ExchangeOrderRecord).filter_by(deal_id=deal_id, side="buy").all()
    levels: list[int] = []
    prefix = f"dca-{deal_id}-"
    for row in rows:
        if row.client_order_id.startswith(prefix):
            try:
                levels.append(int(row.client_order_id[len(prefix):]))
            except ValueError:
                continue
    return max(levels, default=0) + 1
