from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal as D
from typing import Any

from sqlalchemy.orm import Session

from .db import SessionLocal
from .entry_guard import serialized_entry
from .market_data import BinanceSpotMarketData
from .models import AuditEvent, PaperDealRecord, StrategyRecord
from .paper_engine import deserialize_paper_deal, serialize_paper_deal, update_paper_deal
from .risk import strategy_risk_status

logger = logging.getLogger(__name__)


@serialized_entry
def tick_active_paper_deals(db: Session | None = None) -> dict[str, list[dict[str, Any]]]:
    """Update only already-started deals.

    This function deliberately has no entry logic. It cannot discover pairs,
    choose a pair, evaluate a signal, or create a new deal.
    """
    owns_session = db is None
    session = db or SessionLocal()
    updated: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    try:
        rows = session.query(PaperDealRecord).filter_by(status="active").all()
        if not rows:
            return {"updated": updated, "failures": failures}

        if session.get_bind().dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")

        exchange = BinanceSpotMarketData()
        prices: dict[str, D] = {}
        for row in rows:
            savepoint = session.begin_nested()
            try:
                deal = deserialize_paper_deal(row.state_json)
                if deal.symbol not in prices:
                    prices[deal.symbol] = exchange.ticker(deal.symbol).last
                events = update_paper_deal(deal, prices[deal.symbol])
                row.status = deal.status
                row.state_json = serialize_paper_deal(deal)
                event_types = [event.event_type for event in events]
                if events:
                    session.add(AuditEvent(
                        event_type="dca_deal_updated",
                        detail=json.dumps({
                            "deal_id": deal.id,
                            "symbol": deal.symbol,
                            "events": event_types,
                            "status": deal.status,
                        }),
                    ))
                savepoint.commit()
                updated.append({"deal": deal.snapshot(), "events": event_types})
            except Exception as exc:
                if savepoint.is_active:
                    savepoint.rollback()
                logger.warning("DCA deal update failed for %s: %s", row.id, exc)
                failures.append({"deal_id": row.id, "symbol": row.symbol, "error": "Market update or deal processing failed"})

        # Risk is evaluated after marks are persisted. It only blocks future
        # manual entries; it never creates an entry itself.
        for record in session.query(StrategyRecord).all():
            try:
                strategy_risk_status(session, record)
            except Exception as exc:
                logger.warning("DCA risk evaluation failed for config %s: %s", record.id, exc)
                failures.append({"config_id": str(record.id), "symbol": "*", "error": "Risk evaluation failed; new entries remain blocked"})

        session.commit()
        return {"updated": updated, "failures": failures}
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


async def run_paper_worker(stop_event: asyncio.Event, interval_seconds: int) -> None:
    """Low-frequency deal manager. It never opens a new deal."""
    interval = max(3, min(int(interval_seconds), 300))
    while not stop_event.is_set():
        try:
            tick_active_paper_deals()
        except Exception:
            logger.exception("DCA deal worker cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
