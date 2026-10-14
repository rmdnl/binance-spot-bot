from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from sqlalchemy.orm import Session

from .allocation_decision import AutonomousAllocationEngine
from .autonomous import AutonomousDecision
from .entry_intent import create_entry_intent
from .intent_processor import DurableIntentProcessor, IntentProcessStats
from .models import StrategyRecord
from .pair_discovery import AutonomousPairDiscovery
from .testnet_orchestrator import TestnetExecutionGateway

D = Decimal


@dataclass(frozen=True)
class CycleStats:
    discovered: int
    analyzed: int
    intents_created: int
    submitted: int
    recovered: int
    failed: int
    skipped: int


class AutonomousTestnetCycle:
    """Run one durable autonomous Testnet cycle."""

    def __init__(self, session: Session, strategy: StrategyRecord, discovery: AutonomousPairDiscovery,
                 allocation: AutonomousAllocationEngine, gateway: TestnetExecutionGateway,
                 analyze: Callable[[str], AutonomousDecision], reconcile: Callable[[], object],
                 requested_quote: D, max_candidates: int = 10) -> None:
        self.session = session
        self.strategy = strategy
        self.discovery = discovery
        self.allocation = allocation
        self.gateway = gateway
        self.analyze = analyze
        self.reconcile = reconcile
        self.requested_quote = requested_quote
        self.max_candidates = max_candidates

    def run_once(self, open_positions: int) -> CycleStats:
        self.reconcile()
        candidates = self.discovery.discover(limit=self.max_candidates)
        analyzed = intents = skipped = 0
        for candidate in candidates:
            try:
                decision = self.analyze(candidate.symbol)
                analyzed += 1
            except Exception:
                skipped += 1
                continue
            if decision.action != "candidate":
                skipped += 1
                continue
            order = create_entry_intent(
                self.session, self.strategy, decision, self.requested_quote,
                self.allocation, open_positions, self.gateway.adapter,
            )
            if order is None:
                skipped += 1
                continue
            intents += 1

        processed: IntentProcessStats = DurableIntentProcessor(
            self.session, self.strategy, self.gateway
        ).process()
        self.reconcile()
        return CycleStats(
            discovered=len(candidates), analyzed=analyzed, intents_created=intents,
            submitted=processed.submitted, recovered=processed.recovered,
            failed=processed.failed, skipped=skipped,
        )
