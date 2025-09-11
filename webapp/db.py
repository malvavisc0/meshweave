import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# SQLite path configurable via env
SQLITE_PATH = os.getenv("SQLITE_PATH", "/db/app.db")

# Ensure parent dir exists when running in container/local
os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

# SQLAlchemy engine and session factory (sync)
engine = create_engine(
    f"sqlite:///{SQLITE_PATH}",
    connect_args={"check_same_thread": False},  # needed for SQLite with threads
    future=True,
)


# Ensure SQLite enforces foreign key constraints
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        # If this fails, constraints may not be enforced in dev SQLite
        pass


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
)


def init_db() -> None:
    """Create database tables if they do not exist.

    Uses SQLAlchemy metadata to create all tables bound to the configured engine.

    Returns:
        None
    """
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional SQLAlchemy session scope.

    Yields:
        Session: A database session with autoflush/commit disabled.

    Raises:
        Exception: Re-raises any exception from the block after rolling back.

    Notes:
        - Commits if the block exits normally.
        - Rolls back on exception and always closes the session.
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
