from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .intent_queue import pending_entry_intents
from .models import StrategyRecord
from .testnet_orchestrator import SubmitOutcome, TestnetExecutionGateway


@dataclass(frozen=True)
class IntentProcessStats:
    found: int
    submitted: int
    recovered: int
    failed: int


class DurableIntentProcessor:
    """Process persisted intents without blind retries after ambiguous submission."""

    def __init__(self, session: Session, strategy: StrategyRecord, gateway: TestnetExecutionGateway) -> None:
        self.session = session
        self.strategy = strategy
        self.gateway = gateway

    def process(self) -> IntentProcessStats:
        intents = pending_entry_intents(self.session, self.strategy.id)
        submitted = recovered = failed = 0
        for record in intents:
            try:
                outcome: SubmitOutcome = self.gateway.submit_or_recover(record)
            except Exception:
                failed += 1
                continue
            if outcome.submitted:
                submitted += 1
            elif outcome.result is not None:
                recovered += 1
        return IntentProcessStats(len(intents), submitted, recovered, failed)
