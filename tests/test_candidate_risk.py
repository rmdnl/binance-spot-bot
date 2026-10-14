from decimal import Decimal as D

from dca_bot.candidate_risk import CandidateRiskConfig, CandidateRiskGate
from dca_bot.pair_discovery import PairCandidate


def candidate(spread="0.10"):
    return PairCandidate("BTC/USDT", D("10000000"), D(spread), D("100"), D("99.95"), D("100.05"), D("90"))


def gate():
    return CandidateRiskGate(CandidateRiskConfig(D("120"), D("120"), D("0.003")))


def test_accepts_candidate_inside_limits():
    assert gate().evaluate(candidate(), D("100"), 0).allowed


def test_rejects_global_capital_limit():
    g = CandidateRiskGate(CandidateRiskConfig(D("200"), D("120"), D("0.003")))
    assert g.evaluate(candidate(), D("121"), 0).reason == "global_capital_limit"


def test_rejects_max_positions():
    assert gate().evaluate(candidate(), D("100"), 3).reason == "max_positions"


def test_rejects_below_hard_net_profit_floor():
    g = CandidateRiskGate(CandidateRiskConfig(D("120"), D("120"), D("0.002")))
    assert g.evaluate(candidate(), D("100"), 0).reason == "net_profit_floor"
