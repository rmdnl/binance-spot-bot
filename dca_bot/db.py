from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


settings = Settings()


def _sync_database_url(url: str) -> str:
    if url.startswith("sqlite+aiosqlite:///"):
        url = "sqlite:///" + url.removeprefix("sqlite+aiosqlite:///")
    return url


def _prepare_sqlite_path(url: str) -> None:
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url == "sqlite:///:memory:":
        return
    raw_path = url[len(prefix):]
    if not raw_path.startswith("/"):
        target = (Path.cwd() / raw_path).resolve()
    else:
        target = Path(raw_path)
    target.parent.mkdir(parents=True, exist_ok=True)


_sync_url = _sync_database_url(settings.database_url)
_prepare_sqlite_path(_sync_url)
engine = create_engine(_sync_url, connect_args={"check_same_thread": False} if _sync_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    # Import models so metadata is complete before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
