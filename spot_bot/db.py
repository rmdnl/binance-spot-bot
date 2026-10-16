from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import get_settings
from .models import Base

settings = get_settings()
url = settings.database_url
if url.startswith("sqlite+aiosqlite:///"):
    url = "sqlite:///" + url.removeprefix("sqlite+aiosqlite:///")
if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
    raw = url.removeprefix("sqlite:///")
    Path(raw if raw.startswith("/") else Path.cwd() / raw).resolve().parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
