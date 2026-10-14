from decimal import Decimal as D

from dca_bot.allocation_decision import AutonomousAllocationEngine
from dca_bot.capital_allocator import CapitalAllocator
from dca_bot.candidate_risk import CandidateRiskConfig
from dca_bot.models import ExchangeOrderRecord, StrategyRecord, TestnetDealRecord
from dca_bot.pair_discovery import PairCandidate


def candidate():
    return PairCandidate("BTC/USDT", D("10000000"), D("0.10"), D("100"), D("99.95"), D("100.05"), D("90"))


def engine():
    return AutonomousAllocationEngine(
        CapitalAllocator(D("120"), D("50")),
        CandidateRiskConfig(D("50"), D("120"), D("0.003"), max_positions=3),
    )


def test_persisted_active_deal_reduces_available_capital(db_session):
    strategy = StrategyRecord(name="alloc", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    deal = TestnetDealRecord(
        strategy_id=strategy.id, client_deal_id="alloc-1", symbol="ETH/USDT", status="active",
        base_qty="1", quote_cost="100", average_entry="100", realized_pnl="0", dca_level=0, state_json="{}",
    )
    db_session.add(deal)
    db_session.commit()
    decision = engine().decide(db_session, candidate(), D("50"), 1)
    assert decision.allocation.allocated_quote == D("20")
    assert decision.risk.allowed


def test_pending_buy_can_consume_remaining_global_capital(db_session):
    strategy = StrategyRecord(name="alloc2", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    pending = ExchangeOrderRecord(
        strategy_id=strategy.id, symbol="ETH/USDT", side="buy", client_order_id="alloc-buy",
        status="open", amount="2", price="10", filled="0", last_accounted_filled="0", remaining="2", raw_json="{}",
    )
    db_session.add(pending)
    db_session.commit()
    decision = engine().decide(db_session, candidate(), D("50"), 0)
    assert decision.allocation.allocated_quote == D("30")
    assert decision.risk.allowed
