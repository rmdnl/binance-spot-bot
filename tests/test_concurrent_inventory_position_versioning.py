from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from spot_bot.models import Base, Environment, InventoryPosition


def test_stale_inventory_writer_cannot_overwrite_concurrent_position_update(tmp_path):
    database_path = tmp_path / "concurrent-inventory-position.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    with Session(engine) as setup:
        position = InventoryPosition(
            environment=Environment.TESTNET.value,
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            base_quantity=D("0.01"),
            quote_cost=D("800"),
        )
        setup.add(position)
        setup.commit()
        position_id = position.id

    stale_worker = Session(engine)
    fill_worker = Session(engine)
    try:
        stale_position = stale_worker.get(InventoryPosition, position_id)
        fill_position = fill_worker.get(InventoryPosition, position_id)
        assert stale_position is not None
        assert fill_position is not None
        assert stale_position.version_id == fill_position.version_id == 1

        # Worker A applies a fill and commits the new inventory snapshot.
        fill_position.base_quantity = D("0.012")
        fill_position.quote_cost = D("960.16")
        fill_worker.commit()

        # Worker B had read the prior inventory and tries to save stale totals.
        stale_position.base_quantity = D("0.011")
        stale_position.quote_cost = D("880.08")
        with pytest.raises(StaleDataError):
            stale_worker.commit()
        stale_worker.rollback()

        with Session(engine) as verify:
            persisted = verify.scalar(
                select(InventoryPosition).where(InventoryPosition.id == position_id)
            )
            assert persisted is not None
            assert persisted.base_quantity == D("0.012")
            assert persisted.quote_cost == D("960.16")
            assert persisted.version_id == 2
    finally:
        stale_worker.close()
        fill_worker.close()
        engine.dispose()
