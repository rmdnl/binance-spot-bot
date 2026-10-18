import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def run_migration(database_url, revision):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            "upgrade",
            revision,
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_explicit_alembic_config_url_overrides_database_url_environment(tmp_path):
    configured_database = tmp_path / "configured.sqlite"
    environment_database = tmp_path / "environment.sqlite"
    configured_url = f"sqlite:///{configured_database}"
    environment_url = f"sqlite:///{environment_database}"

    decoy_engine = create_engine(environment_url)
    with decoy_engine.begin() as connection:
        connection.execute(text("CREATE TABLE decoy_marker (value TEXT NOT NULL)"))
        connection.execute(
            text("INSERT INTO decoy_marker (value) VALUES ('untouched')")
        )
    decoy_engine.dispose()

    config_content = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    config_content = config_content.replace(
        "script_location = migrations",
        f"script_location = {ROOT / 'migrations'}",
    ).replace(
        "prepend_sys_path = .",
        f"prepend_sys_path = {ROOT}",
    ).replace(
        "sqlalchemy.url = sqlite:///./spot_bot.db",
        f"sqlalchemy.url = {configured_url}",
    )
    config_path = tmp_path / "explicit-alembic.ini"
    config_path.write_text(config_content, encoding="utf-8")

    environment = os.environ.copy()
    environment["DATABASE_URL"] = environment_url
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(config_path),
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    configured_engine = create_engine(configured_url)
    with configured_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "20261018_0006"
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(spot_fills)"))
        }
        assert "environment" in columns
        order_columns = {
            row[1]: row for row in connection.execute(text("PRAGMA table_info(spot_orders)"))
        }
        assert "version_id" in order_columns
        assert str(order_columns["version_id"][4]).strip("'") == "1"
    configured_engine.dispose()

    decoy_engine = create_engine(environment_url)
    with decoy_engine.connect() as connection:
        assert connection.execute(
            text("SELECT value FROM decoy_marker")
        ).scalar_one() == "untouched"
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'alembic_version'"
            )
        ).scalar_one() == 0
    decoy_engine.dispose()



def test_custom_alembic_config_without_url_falls_back_to_database_url_environment(
    tmp_path,
):
    environment_database = tmp_path / "environment-fallback.sqlite"
    environment_url = f"sqlite:///{environment_database}"

    config_content = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    config_content = config_content.replace(
        "script_location = migrations",
        f"script_location = {ROOT / 'migrations'}",
    ).replace(
        "prepend_sys_path = .",
        f"prepend_sys_path = {ROOT}",
    ).replace(
        "sqlalchemy.url = sqlite:///./spot_bot.db\n",
        "",
    )
    config_path = tmp_path / "custom-alembic-without-url.ini"
    config_path.write_text(config_content, encoding="utf-8")

    environment = os.environ.copy()
    environment["DATABASE_URL"] = environment_url
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(config_path),
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(environment_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "20261018_0006"
    engine.dispose()

def insert_order(connection, environment, symbol, suffix):
    result = connection.execute(
        text(
            """
            INSERT INTO spot_orders (
                environment, symbol, side, order_type, status, amount, price,
                filled_amount, average_price, fee_quote, client_order_id,
                exchange_order_id, error_message, created_at, updated_at
            ) VALUES (
                :environment, :symbol, 'buy', 'limit', 'filled', 0.01, 100,
                0.01, 100, 0, :client_order_id, :exchange_order_id, NULL,
                '2026-10-18 00:00:00', '2026-10-18 00:00:00'
            )
            """
        ),
        {
            "environment": environment,
            "symbol": symbol,
            "client_order_id": f"migration-client-{suffix}",
            "exchange_order_id": f"migration-exchange-{suffix}",
        },
    )
    return result.lastrowid


def insert_fill(connection, order_id, symbol, trade_id, environment=None):
    if environment is None:
        statement = """
            INSERT INTO spot_fills (
                order_id, symbol, side, price, amount, quote_amount, fee,
                fee_currency, exchange_trade_id, timestamp
            ) VALUES (
                :order_id, :symbol, 'buy', 100, 0.01, 1, 0, 'USDT',
                :trade_id, '2026-10-18 00:00:00'
            )
        """
        parameters = {"order_id": order_id, "symbol": symbol, "trade_id": trade_id}
    else:
        statement = """
            INSERT INTO spot_fills (
                order_id, environment, symbol, side, price, amount,
                quote_amount, fee, fee_currency, exchange_trade_id, timestamp
            ) VALUES (
                :order_id, :environment, :symbol, 'buy', 100, 0.01, 1,
                0, 'USDT', :trade_id, '2026-10-18 00:00:00'
            )
        """
        parameters = {
            "order_id": order_id,
            "environment": environment,
            "symbol": symbol,
            "trade_id": trade_id,
        }
    connection.execute(text(statement), parameters)


def test_trade_identity_migration_backfills_environment_and_scopes_unique_key(tmp_path):
    database_path = tmp_path / "trade-identity-migration.sqlite"
    database_url = f"sqlite:///{database_path}"
    run_migration(database_url, "20261016_0004")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        testnet_order = insert_order(
            connection, "testnet", "BTC/USDT", "testnet-btc"
        )
        live_order = insert_order(connection, "live", "BTC/USDT", "live-btc")
        insert_fill(connection, testnet_order, "BTC/USDT", "trade-1")
        insert_fill(connection, live_order, "BTC/USDT", "trade-2")
    engine.dispose()

    run_migration(database_url, "head")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        migrated = connection.execute(
            text(
                """
                SELECT o.environment, f.environment, f.symbol, f.exchange_trade_id
                FROM spot_fills AS f
                JOIN spot_orders AS o ON o.id = f.order_id
                ORDER BY f.id
                """
            )
        ).all()
        assert [(row[0], row[1]) for row in migrated] == [
            ("testnet", "testnet"),
            ("live", "live"),
        ]

        testnet_eth = insert_order(
            connection, "testnet", "ETH/USDT", "testnet-eth"
        )
        live_btc = insert_order(connection, "live", "BTC/USDT", "live-btc-same-id")
        insert_fill(connection, testnet_eth, "ETH/USDT", "trade-1", "testnet")
        insert_fill(connection, live_btc, "BTC/USDT", "trade-1", "live")

        count = connection.execute(
            text("SELECT COUNT(*) FROM spot_fills WHERE exchange_trade_id = 'trade-1'")
        ).scalar_one()
        assert count == 3
    engine.dispose()
