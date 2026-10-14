from decimal import Decimal as D

from dca_bot.allocation_decision import AutonomousAllocationEngine
from dca_bot.autonomous import AutonomousDecision, MarketIndicators
from dca_bot.capital_allocator import CapitalAllocator
from dca_bot.candidate_risk import CandidateRiskConfig
from dca_bot.entry_intent import create_entry_intent
from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from dca_bot.pair_discovery import PairCandidate


def _engine() -> AutonomousAllocationEngine:
    return AutonomousAllocationEngine(
        CapitalAllocator(D("120"), D("50")),
        CandidateRiskConfig(D("50"), D("120"), D("0.003"), max_spread_pct=D("0.25"), max_positions=3),
    )


def test_entry_intent_rejects_original_discovery_spread_even_when_prices_are_valid(db_session):
    strategy = StrategyRecord(name="risk", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()

    decision = AutonomousDecision(
        symbol="BTC/USDT",
        timeframe="1h",
        action="candidate",
        reason="test",
        score=90,
        indicators=MarketIndicators(100.0, 101.0, 99.0, 35.0, 1.0, 1.0, 25.0),
        parameters={},
    )
    candidate = PairCandidate(
        "BTC/USDT", D("10000000"), D("0.50"), D("100"), D("99.75"), D("100.25"), D("90")
    )

    order = create_entry_intent(
        db_session,
        strategy,
        decision,
        D("50"),
        _engine(),
        0,
        candidate=candidate,
    )

    assert order is None
    assert db_session.query(ExchangeOrderRecord).count() == 0
    assert db_session.query(TestnetDealRecord).count() == 0
