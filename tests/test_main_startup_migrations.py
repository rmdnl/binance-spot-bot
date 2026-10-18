import asyncio

from spot_bot import main


def test_application_startup_applies_database_migrations(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "init_db", lambda: calls.append("migrated"))

    asyncio.run(main.app.router.startup())

    assert calls == ["migrated"]
