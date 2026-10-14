from __future__ import annotations

import hashlib
from decimal import Decimal

from sqlalchemy.orm import Session

from .allocation_decision import AutonomousAllocationEngine
from .autonomous import AutonomousDecision
from .execution import BinanceSpotTestnetAdapter, ExecutionSafetyError, OrderIntent
from .models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from .pair_discovery import PairCandidate

D = Decimal


def _client_order_id(strategy_id: int, symbol: str, price: D) -> str:
    key = f"entry:{strategy_id}:{symbol}:{price.normalize()}".encode()
    return "entry-" + hashlib.sha256(key).hexdigest()[:32]


def create_entry_intent(session: Session, strategy: StrategyRecord, decision: AutonomousDecision, requested_quote: D, allocation_engine: AutonomousAllocationEngine, open_positions: int, execution: BinanceSpotTestnetAdapter | None = None) -> ExchangeOrderRecord | None:
    if decision.action != "candidate":
        return None
    price = D(str(decision.indicators.close))
    if price <= 0:
        return None
    pair = PairCandidate(decision.symbol, D("0"), D("0"), price, price, price, D(str(decision.score)))
    allocation = allocation_engine.decide(session, pair, requested_quote, open_positions)
    if not allocation.risk.allowed or allocation.allocation.allocated_quote <= 0:
        return None
    amount = allocation.allocation.allocated_quote / price
    if execution is not None:
        try:
            normalized = execution.normalize_limit(OrderIntent(decision.symbol, "buy", amount, price, None, True))
        except (ExecutionSafetyError, ValueError):
            return None
        amount, price = normalized.amount, normalized.price
    client_id = _client_order_id(strategy.id, decision.symbol, price)
    existing = session.query(ExchangeOrderRecord).filter_by(client_order_id=client_id).one_or_none()
    if existing:
        return existing
    deal_id = f"deal-{strategy.id}-{decision.symbol.replace('/', '')}-{client_id[-12:]}"
    deal = TestnetDealRecord(
        strategy_id=strategy.id,
        client_deal_id=deal_id,
        symbol=decision.symbol,
        status="pending",
        base_qty="0",
        quote_cost="0",
        average_entry=None,
        realized_pnl="0",
        dca_level=0,
        state_json="{}",
    )
    session.add(deal)
    session.flush()
    order = ExchangeOrderRecord(
        strategy_id=strategy.id,
        deal_id=deal.id,
        symbol=decision.symbol,
        side="buy",
        client_order_id=client_id,
        status="intent",
        amount=str(amount),
        price=str(price),
        filled="0",
        last_accounted_filled="0",
        remaining=str(amount),
        raw_json="{}",
    )
    session.add(order)
    session.commit()
    return order
