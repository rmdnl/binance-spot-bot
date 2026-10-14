from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .execution_service import ExecutionService, SubmitOutcome
from .intent_queue import pending_entry_intents
from .models import StrategyRecord


@dataclass(frozen=True)
class IntentProcessStats:
    found: int
    submitted: int
    recovered: int
    failed: int


class DurableIntentProcessor:
    """Process persisted intents without blind retries after ambiguous submission."""

    def __init__(self, session: Session, strategy: StrategyRecord, execution: ExecutionService) -> None:
        self.session = session
        self.strategy = strategy
        self.execution = execution

    def process(self) -> IntentProcessStats:
        intents = pending_entry_intents(self.session, self.strategy.id)
        submitted = recovered = failed = 0
        for record in intents:
            try:
                outcome: SubmitOutcome = self.execution.submit_or_recover(record)
            except Exception:
                failed += 1
                continue
            if outcome.submitted:
                submitted += 1
            elif outcome.result is not None:
                recovered += 1
        return IntentProcessStats(len(intents), submitted, recovered, failed)
