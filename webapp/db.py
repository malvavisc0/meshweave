import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# SQLite path configurable via env
SQLITE_PATH = os.getenv("SQLITE_PATH", "/app.db")

# Ensure parent dir exists when running in container/local
os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

# SQLAlchemy engine and session factory (sync)
engine = create_engine(
    f"sqlite:///{SQLITE_PATH}",
    connect_args={"check_same_thread": False},  # needed for SQLite with threads
    future=True,
)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
)


def init_db() -> None:
    """Create DB tables if they do not exist."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
