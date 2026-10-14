from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from .execution_service import ExecutionService, SubmitOutcome
from .models import ExchangeOrderRecord


class Reconciler(Protocol):
    def reconcile(self) -> object: ...


@dataclass(frozen=True)
class CycleResult:
    reconciled: bool
    submitted: int
    skipped: int
    reason: str = ""


class TestnetCycleOrchestrator:
    """Coordinates one safe cycle; reconciliation always precedes submission."""

    def __init__(
        self,
        session: Session,
        execution: ExecutionService,
        reconcile: Callable[[], object],
        risk_gate: Callable[[ExchangeOrderRecord], bool],
    ) -> None:
        self.session = session
        self.execution = execution
        self.reconcile = reconcile
        self.risk_gate = risk_gate

    def run_once(self, intents: list[ExchangeOrderRecord]) -> CycleResult:
        self.reconcile()
        submitted = 0
        skipped = 0
        for record in intents:
            if not self.risk_gate(record):
                skipped += 1
                continue
            outcome: SubmitOutcome = self.execution.submit_or_recover(record)
            if outcome.submitted:
                submitted += 1
            else:
                skipped += 1
        return CycleResult(True, submitted, skipped)
