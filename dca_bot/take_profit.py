from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import ExchangeOrderRecord
from .net_profit import required_exit_price

D = Decimal


@dataclass(frozen=True)
class TakeProfitSpec:
    deal_id: int
    strategy_id: int
    symbol: str
    quantity: D
    entry_price: D
    target_net_pct: D
    buy_fee_pct: D
    sell_fee_pct: D
    slippage_pct: D

    @property
    def target_price(self) -> D:
        return required_exit_price(
            self.entry_price,
            self.target_net_pct,
            self.buy_fee_pct,
            self.sell_fee_pct,
            self.slippage_pct,
        )


def take_profit_client_order_id(deal_id: int) -> str:
    if deal_id <= 0:
        raise ValueError("invalid deal id")
    return f"tp-{deal_id}"


def get_or_create_take_profit_intent(session: Session, spec: TakeProfitSpec) -> ExchangeOrderRecord:
    if spec.quantity <= 0:
        raise ValueError("take profit quantity must be positive")
    target = spec.target_price
    if target <= 0:
        raise ValueError("take profit target price must be positive")

    cid = take_profit_client_order_id(spec.deal_id)
    existing = session.query(ExchangeOrderRecord).filter_by(client_order_id=cid).one_or_none()
    if existing:
        if existing.deal_id != spec.deal_id or existing.symbol != spec.symbol or existing.side != "sell":
            raise RuntimeError("take profit client order id collision")
        return existing

    row = ExchangeOrderRecord(
        strategy_id=spec.strategy_id,
        deal_id=spec.deal_id,
        symbol=spec.symbol,
        side="sell",
        client_order_id=cid,
        status="intent",
        amount=str(spec.quantity),
        price=str(target),
        filled="0",
        last_accounted_filled="0",
        remaining=str(spec.quantity),
        raw_json="{}",
    )
    session.add(row)
    session.flush()
    return row
