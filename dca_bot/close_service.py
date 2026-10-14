from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import AuditEvent, ExchangeOrderRecord, TestnetDealRecord
from .take_profit import TakeProfitSpec, get_or_create_take_profit_intent

D = Decimal


@dataclass(frozen=True)
class CloseDecision:
    should_close: bool
    target_price: D | None = None
    reason: str = ""


def evaluate_take_profit(
    deal: TestnetDealRecord,
    market_price: D,
    target_net_pct: D,
    buy_fee_pct: D,
    sell_fee_pct: D,
    slippage_pct: D,
) -> CloseDecision:
    if deal.status != "active":
        return CloseDecision(False, reason="deal_not_active")
    qty = D(deal.base_qty)
    avg = D(deal.average_entry or "0")
    if qty <= 0 or avg <= 0 or market_price <= 0:
        return CloseDecision(False, reason="no_position")
    if target_net_pct < 0:
        raise ValueError("target_net_pct must be non-negative")
    target = TakeProfitSpec(
        deal_id=deal.id,
        strategy_id=deal.strategy_id,
        symbol=deal.symbol,
        quantity=qty,
        entry_price=avg,
        target_net_pct=target_net_pct,
        buy_fee_pct=buy_fee_pct,
        sell_fee_pct=sell_fee_pct,
        slippage_pct=slippage_pct,
    ).target_price
    return CloseDecision(
        market_price >= target,
        target_price=target,
        reason="target_reached" if market_price >= target else "target_not_reached",
    )


def get_or_create_close_intent(session: Session, deal: TestnetDealRecord, spec: TakeProfitSpec) -> ExchangeOrderRecord:
    qty = D(deal.base_qty)
    if deal.status != "active":
        raise RuntimeError("cannot create close order for inactive deal")
    if qty <= 0:
        raise RuntimeError("cannot close an empty position")
    if spec.deal_id != deal.id or spec.symbol != deal.symbol or spec.quantity > qty:
        raise RuntimeError("close specification does not match position")
    return get_or_create_take_profit_intent(session, spec)


def mark_close_filled(session: Session, deal: TestnetDealRecord, order: ExchangeOrderRecord) -> None:
    if order.deal_id != deal.id or order.side != "sell":
        raise RuntimeError("close order identity mismatch")
    if D(order.filled) < D(order.amount):
        return
    if D(order.last_accounted_filled) < D(order.filled):
        raise RuntimeError("close fills must be accounted before closing deal")
    if D(deal.base_qty) != 0:
        raise RuntimeError("cannot mark deal closed while position remains")
    deal.status = "closed"
    session.add(AuditEvent(event_type="testnet_deal_closed", detail=f"deal={deal.id} order={order.id}"))
    session.commit()
