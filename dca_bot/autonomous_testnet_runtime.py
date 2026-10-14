from __future__ import annotations

import logging
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from .allocation_decision import AutonomousAllocationEngine
from .autonomous import AutonomousDecision, analyze_market
from .capital_allocator import CapitalAllocator
from .candidate_risk import CandidateRiskConfig
from .db import SessionLocal
from .execution_factory import build_execution_adapter
from .intent_processor import DurableIntentProcessor
from .market_data import BinanceSpotMarketData
from .models import StrategyRecord, TestnetDealRecord
from .order_state import reconcile_orders
from .pair_discovery import AutonomousPairDiscovery
from .strategy_runtime import StrategyRuntimeError, load_strategy_config
from .testnet_cycle import AutonomousTestnetCycle
from .testnet_orchestrator import TestnetExecutionGateway

logger = logging.getLogger(__name__)
D = Decimal


def _open_positions(session: Session, strategy_id: int) -> int:
    return int(
        session.scalar(
            func.count(TestnetDealRecord.id).where(
                TestnetDealRecord.strategy_id == strategy_id,
                TestnetDealRecord.status == "active",
            )
        )
        or 0
    )


def run_testnet_cycle_once(settings: Any) -> dict[str, Any]:
    """Execute one autonomous Testnet pass using the canonical order lifecycle."""
    settings.assert_safe()
    if settings.trading_mode.lower().strip() != "testnet":
        raise RuntimeError("Autonomous Testnet runtime requires TRADING_MODE=testnet")

    session = SessionLocal()
    adapter = None
    try:
        adapter = build_execution_adapter(settings)
        market_data = BinanceSpotMarketData()
        discovery = AutonomousPairDiscovery(market_data)
        strategies = session.query(StrategyRecord).filter_by(enabled=True).all()
        results: list[dict[str, Any]] = []

        for strategy in strategies:
            try:
                config = load_strategy_config(strategy)
            except StrategyRuntimeError as exc:
                logger.error("Strategy %s blocked: %s", strategy.id, exc)
                continue
            if config.start_condition != "autonomous" or config.pair_selection_mode != "automatic_usdt":
                continue

            allocation = AutonomousAllocationEngine(
                CapitalAllocator(
                    D(str(config.max_total_allocation)),
                    D(str(config.max_capital_per_deal)),
                ),
                CandidateRiskConfig(
                    max_capital_quote=D(str(config.max_capital_per_deal)),
                    global_capital_limit_quote=D(str(config.max_total_allocation)),
                    # StrategyConfig stores percentages (0.3 = 0.30%); risk gate uses fractions.
                    min_net_profit_pct=D(str(config.min_net_profit_pct)) / D("100"),
                    max_spread_pct=D(str(config.discovery_max_spread_pct)),
                    max_positions=config.max_active_deals,
                ),
            )
            gateway = TestnetExecutionGateway(adapter, session)

            def analyze(symbol: str, *, _config=config) -> AutonomousDecision:
                candles = market_data.candles(symbol, _config.timeframe, limit=min(1000, max(_config.ema_slow + 50, 300)))
                if len(candles) < 2:
                    raise ValueError("not enough candles")
                return analyze_market(symbol, _config.timeframe, candles[:-1], _config)

            def reconcile(*, _strategy_id=strategy.id) -> object:
                return reconcile_orders(session, adapter, _strategy_id)

            cycle = AutonomousTestnetCycle(
                session=session,
                strategy=strategy,
                discovery=discovery,
                allocation=allocation,
                gateway=gateway,
                analyze=analyze,
                reconcile=reconcile,
                requested_quote=D(str(config.base_order_quote)),
                max_candidates=config.discovery_max_candidates,
            )
            stats = cycle.run_once(_open_positions(session, strategy.id))
            results.append({"strategy_id": strategy.id, **asdict(stats)})

        return {"strategies": results}
    finally:
        session.close()
