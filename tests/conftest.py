import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dca_bot.db import Base
from dca_bot import models  # noqa: F401


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
