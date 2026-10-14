from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .pair_discovery import PairCandidate

D = Decimal


@dataclass(frozen=True)
class CandidateRiskConfig:
    max_capital_quote: D
    global_capital_limit_quote: D
    min_net_profit_pct: D
    max_spread_pct: D = D("0.25")
    max_positions: int = 3


@dataclass(frozen=True)
class CandidateDecision:
    allowed: bool
    reason: str


class CandidateRiskGate:
    def __init__(self, config: CandidateRiskConfig) -> None:
        self.config = config

    def evaluate(self, candidate: PairCandidate, allocated_quote: D, open_positions: int) -> CandidateDecision:
        if candidate.spread_pct > self.config.max_spread_pct:
            return CandidateDecision(False, "spread_too_wide")
        if allocated_quote < 0:
            return CandidateDecision(False, "invalid_allocated_capital")
        if allocated_quote > self.config.max_capital_quote:
            return CandidateDecision(False, "deal_capital_limit")
        if allocated_quote > self.config.global_capital_limit_quote:
            return CandidateDecision(False, "global_capital_limit")
        if open_positions >= self.config.max_positions:
            return CandidateDecision(False, "max_positions")
        if self.config.min_net_profit_pct < D("0.003"):
            return CandidateDecision(False, "net_profit_floor")
        if candidate.last <= 0 or candidate.bid <= 0 or candidate.ask <= 0:
            return CandidateDecision(False, "invalid_market_price")
        return CandidateDecision(True, "accepted")
