from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .capital_allocator import CapitalAllocation, CapitalAllocator
from .capital_state import allocated_quote
from .candidate_risk import CandidateDecision, CandidateRiskConfig, CandidateRiskGate
from .pair_discovery import PairCandidate

D = Decimal


@dataclass(frozen=True)
class AllocationDecision:
    candidate: PairCandidate
    allocation: CapitalAllocation
    risk: CandidateDecision


class AutonomousAllocationEngine:
    """Derives allocation from persisted state before evaluating a candidate."""

    def __init__(self, allocator: CapitalAllocator, risk_config: CandidateRiskConfig) -> None:
        self.allocator = allocator
        self.risk_gate = CandidateRiskGate(risk_config)

    def decide(self, session: Session, candidate: PairCandidate, requested_quote: D, open_positions: int) -> AllocationDecision:
        current = allocated_quote(session)
        allocation = self.allocator.allocate(requested_quote, current)
        risk = self.risk_gate.evaluate(candidate, allocation.allocated_quote, open_positions)
        if allocation.allocated_quote <= 0:
            risk = CandidateDecision(False, allocation.reason)
        return AllocationDecision(candidate, allocation, risk)
