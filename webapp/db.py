import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

# SQLite path configurable via env
SQLITE_PATH = os.getenv("SQLITE_PATH", "/db/app.db")

# Ensure parent dir exists when running in container/local
os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

# SQLAlchemy engine and session factory (sync)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


class DatabaseConnectionPool:
    """Thread-safe singleton for SQLAlchemy Engine and Session factory."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, database_url: str, sqlite_path: str):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialize_pool(database_url, sqlite_path)
                    cls._instance = instance
        return cls._instance

    def _initialize_pool(self, database_url: str, sqlite_path: str) -> None:
        url = (database_url or "").strip()
        if url:
            # External DB (e.g., Postgres)
            try:
                pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
                max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
                pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))
                pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
            except Exception:
                # Fallback to safe defaults
                pool_size, max_overflow, pool_recycle, pool_timeout = 5, 10, 1800, 30
            self.engine = create_engine(
                url,
                future=True,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_recycle=pool_recycle,
                pool_timeout=pool_timeout,
            )
            if self.engine.dialect.name == "sqlite":
                log.warning(
                    "DATABASE_URL is set but SQLite dialect detected (%s). Verify configuration.",
                    url,
                )
                event.listen(self.engine, "connect", _set_sqlite_pragma)
        else:
            # Default to SQLite
            self.engine = create_engine(
                f"sqlite:///{sqlite_path}",
                connect_args={"check_same_thread": False, "timeout": 30.0},
                future=True,
            )
            event.listen(self.engine, "connect", _set_sqlite_pragma)

        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
            expire_on_commit=False,
        )

    def get_session(self) -> Session:
        return self.SessionLocal()


# Ensure SQLite enforces foreign key constraints (register only for SQLite)
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite foreign key enforcement on each new connection.

    Args:
        dbapi_connection: DB-API connection object for the SQLite database.
        connection_record: SQLAlchemy connection record (not used).

    Returns:
        None
    """
    try:
        cursor = dbapi_connection.cursor()
        # Enforce FKs
        cursor.execute("PRAGMA foreign_keys=ON")
        # Improve concurrency and reduce lock errors
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception as e:
            log.warning("SQLite PRAGMA journal_mode=WAL failed: %s", e)
        try:
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception as e:
            log.warning("SQLite PRAGMA synchronous=NORMAL failed: %s", e)
        # Additional safety: busy timeout in milliseconds
        try:
            cursor.execute("PRAGMA busy_timeout=10000")
        except Exception as e:
            log.warning("SQLite PRAGMA busy_timeout failed: %s", e)
        cursor.close()
    except Exception as e:
        # If this fails, constraints may not be enforced in dev SQLite
        log.warning("SQLite PRAGMA setup failed: %s", e)


db_pool = DatabaseConnectionPool(DATABASE_URL, SQLITE_PATH)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "" if not default else "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _run_alembic_upgrade() -> None:
    """Apply Alembic migrations to head.

    Configures Alembic from alembic.ini and runs `upgrade head` in-process.
    The database URL is resolved by alembic/env.py from DATABASE_URL/SQLITE_PATH.
    """
    try:
        from alembic.command import upgrade
        from alembic.config import Config

        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "alembic")
        upgrade(cfg, "head")
        log.info("init_db: alembic upgrade head completed")
    except Exception as e:
        log.error("init_db: alembic upgrade head failed: %s", e)
        raise


def init_db() -> None:
    """Apply Alembic migrations to head.

    Runs by default for SQLite (local dev has no out-of-band migration
    runner); for other dialects only when WEBAPP_AUTO_MIGRATE is set.
    """
    dialect = db_pool.engine.dialect.name
    if _env_bool("WEBAPP_AUTO_MIGRATE", dialect == "sqlite"):
        _run_alembic_upgrade()
    else:
        log.info("init_db: dialect=%s, path=manual_migrate", dialect)


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
    session: Session = db_pool.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a database session for request scope.

    Yields:
        Session: A database session. Callers should not commit; writes should
        explicitly manage transactions, while simple read paths typically rely
        on autocommit-less sessions.
    """
    session: Session = db_pool.get_session()
    try:
        yield session
    finally:
        session.close()
