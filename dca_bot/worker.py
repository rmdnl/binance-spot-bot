from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .db import SessionLocal
from .market_data import BinanceSpotMarketData
from .models import AuditEvent, PaperDealRecord, StrategyRecord
from .strategy import StrategyConfig
from .paper_engine import deserialize_paper_deal, serialize_paper_deal, update_paper_deal

logger = logging.getLogger(__name__)


def tick_active_paper_deals(db: Session | None = None) -> dict[str, list[dict[str, Any]]]:
    """Poll each active symbol once and persist the deterministic paper state transitions."""
    owns_session = db is None
    session = db or SessionLocal()
    updated: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    try:
        rows = session.query(PaperDealRecord).filter_by(status="active").all()
        if not rows:
            return {"updated": updated, "failures": failures}
        exchange = BinanceSpotMarketData()
        prices = {}
        for row in rows:
            try:
                deal = deserialize_paper_deal(row.state_json)
                if deal.symbol not in prices:
                    prices[deal.symbol] = exchange.ticker(deal.symbol).last
                events = update_paper_deal(deal, prices[deal.symbol])
                row.status = deal.status
                row.state_json = serialize_paper_deal(deal)
                event_types = [event.event_type for event in events]
                updated.append({"deal": deal.snapshot(), "events": event_types})
                if events:
                    session.add(AuditEvent(
                        event_type="paper_deal_updated",
                        detail=json.dumps({
                            "deal_id": deal.id,
                            "symbol": deal.symbol,
                            "events": event_types,
                            "status": deal.status,
                        }),
                    ))
                if (
                    deal.status == "closed"
                    and deal.config.reinvest_profit
                    and deal.realized_pnl_quote > 0
                    and "deal_closed" in event_types
                ):
                    strategy_record = session.get(StrategyRecord, row.strategy_id)
                    if strategy_record is not None:
                        try:
                            prior = StrategyConfig.model_validate_json(strategy_record.config_json)
                            candidate = StrategyConfig.model_validate({
                                **prior.model_dump(),
                                "base_order_quote": prior.base_order_quote + float(deal.realized_pnl_quote),
                            })
                            required = candidate.estimated_max_deal_capital(float(deal.last_price))
                            if required <= candidate.max_capital_per_deal and required * candidate.max_active_deals <= candidate.max_total_allocation:
                                strategy_record.config_json = candidate.model_dump_json()
                                strategy_record.name = candidate.name
                                session.add(AuditEvent(
                                    event_type="paper_profit_reinvested",
                                    detail=json.dumps({
                                        "deal_id": deal.id,
                                        "strategy_id": row.strategy_id,
                                        "profit_quote": str(deal.realized_pnl_quote),
                                        "next_base_order_quote": candidate.base_order_quote,
                                    }),
                                ))
                            else:
                                session.add(AuditEvent(
                                    event_type="paper_reinvestment_blocked",
                                    detail=json.dumps({"deal_id": deal.id, "reason": "capital cap would be exceeded"}),
                                ))
                        except ValueError:
                            session.add(AuditEvent(
                                event_type="paper_reinvestment_blocked",
                                detail=json.dumps({"deal_id": deal.id, "reason": "updated strategy failed validation"}),
                            ))
            except Exception as exc:
                logger.warning("Paper deal poll failed for %s: %s", row.id, exc)
                failures.append({
                    "deal_id": row.id,
                    "symbol": row.symbol,
                    "error": "Market update or state processing failed",
                })
        session.commit()
        return {"updated": updated, "failures": failures}
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


async def run_paper_worker(stop_event: asyncio.Event, interval_seconds: int) -> None:
    """Single-process paper-only polling loop. Never submits exchange orders."""
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(tick_active_paper_deals)
        except Exception:
            logger.exception("Paper DCA worker iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
