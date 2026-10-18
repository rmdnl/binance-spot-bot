import asyncio

from spot_bot import main


def test_application_startup_applies_database_migrations(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "init_db", lambda: calls.append("migrated"))

    async def run_lifespan():
        async with main.lifespan(main.app):
            assert calls == ["migrated"]

    asyncio.run(run_lifespan())

    assert calls == ["migrated"]
