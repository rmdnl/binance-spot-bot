from dca_bot.models import ExchangeOrderRecord, StrategyRecord
from dca_bot.orchestrator import TestnetCycleOrchestrator
from dca_bot.testnet_orchestrator import TestnetExecutionGateway


class Adapter:
    def __init__(self):
        self.events = []

    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.events.append("lookup")
        return []

    def place_limit(self, intent):
        self.events.append("submit")
        raise AssertionError("submission should be blocked by test risk gate")


def test_reconcile_happens_before_risk_and_submission(db_session):
    strategy = StrategyRecord(name="orchestrator", config_json="{}", enabled=True)
    db_session.add(strategy)
    db_session.commit()
    record = ExchangeOrderRecord(
        strategy_id=strategy.id, symbol="BTC/USDT", side="buy", client_order_id="x-1",
        status="intent", amount="1", price="100", filled="0", last_accounted_filled="0",
        remaining="1", raw_json="{}",
    )
    db_session.add(record)
    db_session.commit()

    events = []
    adapter = Adapter()
    orchestrator = TestnetCycleOrchestrator(
        db_session,
        TestnetExecutionGateway(adapter, db_session),
        reconcile=lambda: events.append("reconcile"),
        risk_gate=lambda _: events.append("risk") or False,
    )
    result = orchestrator.run_once([record])
    assert result.reconciled
    assert result.submitted == 0
    assert events == ["reconcile", "risk"]
    assert adapter.events == []
