from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings


class Base(DeclarativeBase):
    pass


def _prepare_sqlite_path(url: str) -> None:
    prefix = 'sqlite:///'
    if url.startswith(prefix) and url != 'sqlite:///:memory:':
        file_path = url[len(prefix):]
        if file_path and not file_path.startswith('/'):
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)


def build_engine():
    settings = get_settings()
    _prepare_sqlite_path(settings.database_url)
    kwargs = {'check_same_thread': False} if settings.database_url.startswith('sqlite') else {}
    return create_engine(settings.database_url, connect_args=kwargs, future=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401
    from .migrations import ensure_schema
    ensure_schema(engine)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
