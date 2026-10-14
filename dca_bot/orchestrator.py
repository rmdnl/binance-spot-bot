from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from .models import ExchangeOrderRecord
from .testnet_orchestrator import SubmitOutcome, TestnetExecutionGateway


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
        gateway: TestnetExecutionGateway,
        reconcile: Callable[[], object],
        risk_gate: Callable[[ExchangeOrderRecord], bool],
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.reconcile = reconcile
        self.risk_gate = risk_gate

    def run_once(self, intents: list[ExchangeOrderRecord]) -> CycleResult:
        # Never create new exchange exposure from stale local state.
        self.reconcile()
        submitted = 0
        skipped = 0
        for record in intents:
            if not self.risk_gate(record):
                skipped += 1
                continue
            outcome: SubmitOutcome = self.gateway.submit_or_recover(record)
            if outcome.submitted:
                submitted += 1
            else:
                skipped += 1
        return CycleResult(True, submitted, skipped)
