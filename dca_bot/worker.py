from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from .db import SessionLocal
from .market_data import BinanceSpotMarketData
from .autonomous import analyze_market
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


def scan_autonomous_strategies(db: Session | None = None) -> dict[str, Any]:
    """Evaluate enabled autonomous profiles and open paper deals only on a fresh closed candle."""
    owns_session = db is None
    session = db or SessionLocal()
    result: dict[str, Any] = {"started": [], "waited": [], "failures": []}
    try:
        profiles = session.query(StrategyRecord).filter_by(enabled=True).all()
        profiles = [
            row for row in profiles
            if StrategyConfig.model_validate_json(row.config_json).start_condition == "autonomous"
        ]
        if not profiles:
            return result

        decision_events = session.query(AuditEvent).filter_by(
            event_type="autonomous_decision"
        ).order_by(AuditEvent.id.desc()).limit(1000).all()
        last_decision: dict[tuple[int, str], dict[str, Any]] = {}
        for event in decision_events:
            try:
                detail = json.loads(event.detail)
                key = (int(detail["strategy_id"]), str(detail["symbol"]))
                last_decision.setdefault(key, detail)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue

        entry_events = session.query(AuditEvent).filter_by(
            event_type="autonomous_entry_started"
        ).order_by(AuditEvent.id.desc()).limit(1000).all()
        last_entry: dict[tuple[int, str], float] = {}
        for event in entry_events:
            try:
                detail = json.loads(event.detail)
                key = (int(detail["strategy_id"]), str(detail["symbol"]))
                last_entry.setdefault(key, float(detail["started_at_epoch"]))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue

        exchange = BinanceSpotMarketData()
        now = time.time()
        for profile in profiles:
            try:
                config = StrategyConfig.model_validate_json(profile.config_json)
            except ValueError as exc:
                result["failures"].append({"strategy_id": profile.id, "error": str(exc)})
                continue
            for symbol in config.symbols:
                key = (profile.id, symbol)
                try:
                    active_rows = session.query(PaperDealRecord).filter_by(status="active").all()
                    active_deals = [
                        (row, deserialize_paper_deal(row.state_json)) for row in active_rows
                    ]
                    strategy_deals = [item for item in active_deals if item[0].strategy_id == profile.id]
                    symbol_deals = [item for item in strategy_deals if item[1].symbol == symbol]
                    if len(strategy_deals) >= config.max_active_deals:
                        result["waited"].append({"strategy_id": profile.id, "symbol": symbol, "reason": "active deal limit"})
                        continue
                    if len(symbol_deals) >= config.max_active_deals_per_symbol:
                        result["waited"].append({"strategy_id": profile.id, "symbol": symbol, "reason": "pair already has an active deal"})
                        continue

                    limit = min(1000, max(config.ema_slow + 50, config.atr_period * 3 + 10, 300))
                    candles = exchange.candles(symbol, config.timeframe, limit=limit)
                    if len(candles) < 2:
                        raise ValueError("Not enough closed Spot candles")
                    closed_candles = candles[:-1]  # Do not make a decision on the still-forming candle.
                    candle_timestamp_ms = int(closed_candles[-1][0])
                    prior = last_decision.get(key, {})
                    if prior.get("candle_timestamp_ms") == candle_timestamp_ms:
                        continue

                    decision = analyze_market(symbol, config.timeframe, closed_candles, config)
                    action = decision.action
                    reason = decision.reason
                    params = decision.parameters
                    if action == "candidate":
                        last_started = last_entry.get(key, 0.0)
                        if now - last_started < config.cooldown_seconds:
                            action, reason = "wait", "Configured cooldown has not elapsed"
                        else:
                            required = float(params.get("estimated_deal_capital_quote", float("inf")))
                            used_capacity = sum(
                                float(deal.config.max_capital_per_deal) for _, deal in strategy_deals
                            )
                            if used_capacity + required > config.max_total_allocation:
                                action, reason = "wait", "Total allocation cap would be exceeded"
                            elif not params.get("capital_safe", False):
                                action, reason = "wait", "Adaptive DCA ladder exceeds the capital cap"
                            else:
                                adaptive_config = config.model_copy(update={
                                    "symbols": [symbol],
                                    "start_condition": "immediately",
                                    "entry_filter": "none",
                                    "initial_deviation_pct": float(params["initial_deviation_pct"]),
                                    "deviation_scale": float(params["deviation_scale"]),
                                    "take_profit_pct": float(params["take_profit_pct"]),
                                    "max_safety_orders": int(params["max_safety_orders"]),
                                    "safety_order_volume_scale": float(params["safety_order_volume_scale"]),
                                })
                                price = float(closed_candles[-1][4])
                                deal = start_paper_deal(adaptive_config, symbol, price)
                                session.add(PaperDealRecord(
                                    id=deal.id,
                                    strategy_id=profile.id,
                                    symbol=deal.symbol,
                                    status=deal.status,
                                    state_json=serialize_paper_deal(deal),
                                ))
                                started_at = time.time()
                                last_entry[key] = started_at
                                result["started"].append({
                                    "strategy_id": profile.id,
                                    "symbol": symbol,
                                    "deal": deal.snapshot(),
                                })
                                session.add(AuditEvent(
                                    event_type="autonomous_entry_started",
                                    detail=json.dumps({
                                        "strategy_id": profile.id,
                                        "symbol": symbol,
                                        "deal_id": deal.id,
                                        "started_at_epoch": started_at,
                                        "candle_timestamp_ms": candle_timestamp_ms,
                                        "parameters": params,
                                    }),
                                ))
                    detail = {
                        "strategy_id": profile.id,
                        "symbol": symbol,
                        "timeframe": config.timeframe,
                        "candle_timestamp_ms": candle_timestamp_ms,
                        "evaluated_at_epoch": now,
                        "action": action,
                        "reason": reason,
                        "score": decision.score,
                        "indicators": {
                            "close": decision.indicators.close,
                            "ema_fast": decision.indicators.ema_fast,
                            "ema_slow": decision.indicators.ema_slow,
                            "rsi": decision.indicators.rsi,
                            "atr_pct": decision.indicators.atr_pct,
                            "adx": decision.indicators.adx,
                        },
                        "parameters": params,
                    }
                    session.add(AuditEvent(
                        event_type="autonomous_decision",
                        detail=json.dumps(detail),
                    ))
                    last_decision[key] = detail
                    if action != "candidate":
                        result["waited"].append({
                            "strategy_id": profile.id,
                            "symbol": symbol,
                            "reason": reason,
                            "score": decision.score,
                        })
                except Exception as exc:
                    logger.warning("Autonomous paper scan failed for strategy %s / %s: %s", profile.id, symbol, exc)
                    result["failures"].append({
                        "strategy_id": profile.id,
                        "symbol": symbol,
                        "error": "Market analysis or paper entry failed; no exchange order was sent",
                    })
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


async def run_paper_worker(stop_event: asyncio.Event, interval_seconds: int) -> None:
    """Single-process paper-only loop. It never submits exchange orders."""
    last_autonomous_scan = 0.0
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(tick_active_paper_deals)
        except Exception:
            logger.exception("Paper DCA worker iteration failed")
        now = time.monotonic()
        if now - last_autonomous_scan >= max(60, interval_seconds):
            try:
                await asyncio.to_thread(scan_autonomous_strategies)
            except Exception:
                logger.exception("Autonomous paper strategy scan failed")
            last_autonomous_scan = time.monotonic()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
