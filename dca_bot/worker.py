from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .db import SessionLocal
from .market_data import BinanceSpotMarketData
from .models import AuditEvent, PaperDealRecord
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
