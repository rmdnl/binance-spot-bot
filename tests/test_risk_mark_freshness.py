from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from dca_bot.db import Base
from dca_bot.models import PaperDealRecord, StrategyRecord
from dca_bot.paper_engine import serialize_paper_deal, start_paper_deal
from dca_bot.risk import strategy_risk_status
from dca_bot.strategy import StrategyConfig


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    return engine, session


def _active_deal(session):
    config = StrategyConfig(
        name="risk-freshness-test",
        symbols=["BTC/USDT"],
        base_order_quote=10,
        safety_order_quote=10,
        max_safety_orders=0,
        take_profit_pct=1,
        max_capital_per_deal=100,
        max_total_allocation=300,
    )
    strategy = StrategyRecord(
        name=config.name,
        config_json=config.model_dump_json(),
        enabled=True,
    )
    session.add(strategy)
    session.flush()
    deal = start_paper_deal(config, "BTC/USDT", D("100"))
    row = PaperDealRecord(
        id=deal.id,
        strategy_id=strategy.id,
        symbol=deal.symbol,
        status=deal.status,
        state_json=serialize_paper_deal(deal),
    )
    session.add(row)
    session.commit()
    return strategy, row


def test_risk_evaluation_fails_closed_when_active_paper_mark_is_stale():
    engine, session = _session()
    try:
        strategy, row = _active_deal(session)
        now = datetime.now(timezone.utc)
        session.execute(
            update(PaperDealRecord)
            .where(PaperDealRecord.id == row.id)
            .values(updated_at=now - timedelta(minutes=10))
        )
        session.commit()

        with pytest.raises(ValueError, match="mark is stale"):
            strategy_risk_status(session, strategy, now=now)
    finally:
        session.close()
        engine.dispose()


def test_risk_evaluation_accepts_a_fresh_active_paper_mark():
    engine, session = _session()
    try:
        strategy, _ = _active_deal(session)

        result = strategy_risk_status(session, strategy, now=datetime.now(timezone.utc))

        assert result["blocked"] is False
        assert result["equity_quote"]
    finally:
        session.close()
        engine.dispose()
