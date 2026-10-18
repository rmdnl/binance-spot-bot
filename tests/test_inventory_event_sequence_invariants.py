import random
from decimal import Decimal as D

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from spot_bot.inventory_service import apply_fill
from spot_bot.models import (
    Base,
    Environment,
    InventoryApplication,
    InventoryPosition,
    RealizedPNLEntry,
    SpotFill,
    SpotOrder,
)


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_fill(db: Session, seed: int, index: int, side: str, units: int, price: int) -> SpotFill:
    amount = D(units) / D("1000")
    quote_amount = amount * D(price)
    order = SpotOrder(
        environment=Environment.TESTNET.value,
        symbol="BTC/USDT",
        side=side,
        order_type="limit",
        status="filled",
        amount=amount,
        price=D(price),
        client_order_id=f"sequence-{seed}-{index}",
        exchange_order_id=f"exchange-sequence-{seed}-{index}",
    )
    db.add(order)
    db.flush()
    fill = SpotFill(
        order_id=order.id,
        exchange_trade_id=f"trade-sequence-{seed}-{index}",
        symbol="BTC/USDT",
        side=side,
        amount=amount,
        price=D(price),
        quote_amount=quote_amount,
        fee=D("0.08"),
        fee_currency="USDT",
    )
    db.add(fill)
    db.commit()
    return fill


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_seeded_order_event_sequences_preserve_inventory_and_pnl_invariants():
    # Deterministic fuzz-style event sequences keep failures reproducible in CI.
    for seed in (7, 29, 101):
        rng = random.Random(seed)
        with _make_session() as db:
            fills: list[SpotFill] = []
            expected_units = 0
            sell_count = 0

            for index in range(100):
                side = "buy" if expected_units == 0 or rng.random() < 0.6 else "sell"
                units = rng.randint(1, 5) if side == "buy" else rng.randint(1, expected_units)
                price = rng.randint(78_000, 83_000)
                fill = _make_fill(db, seed, index, side, units, price)
                fills.append(fill)

                result = apply_fill(db, fill)
                assert result.applied is True
                db.commit()

                if side == "buy":
                    expected_units += units
                else:
                    expected_units -= units
                    sell_count += 1

                # Every fill replay must be a no-op, including the PNL ledger.
                replay = apply_fill(db, fill)
                assert replay.applied is False
                db.commit()

                position = db.scalar(
                    select(InventoryPosition).where(
                        InventoryPosition.environment == Environment.TESTNET.value,
                        InventoryPosition.symbol == "BTC/USDT",
                    )
                )
                assert position is not None
                assert position.base_quantity == D(expected_units) / D("1000")
                assert position.base_quantity >= 0
                assert position.quote_cost >= 0
                assert _count(db, SpotFill) == index + 1
                assert _count(db, InventoryApplication) == index + 1
                assert _count(db, RealizedPNLEntry) == sell_count

                pnl_rows = db.scalars(select(RealizedPNLEntry)).all()
                assert sum((row.realized_pnl for row in pnl_rows), D("0")) == sum(
                    (row.proceeds - row.entry_cost for row in pnl_rows), D("0")
                )

            # Replaying the entire history in a different order must not mutate
            # the final inventory or duplicate any accounting ledger entries.
            position = db.scalar(
                select(InventoryPosition).where(
                    InventoryPosition.environment == Environment.TESTNET.value,
                    InventoryPosition.symbol == "BTC/USDT",
                )
            )
            final_state = (
                position.base_quantity,
                position.quote_cost,
                _count(db, SpotFill),
                _count(db, InventoryApplication),
                _count(db, RealizedPNLEntry),
            )
            rng.shuffle(fills)
            for fill in fills:
                assert apply_fill(db, fill).applied is False
            db.commit()

            assert (
                position.base_quantity,
                position.quote_cost,
                _count(db, SpotFill),
                _count(db, InventoryApplication),
                _count(db, RealizedPNLEntry),
            ) == final_state
