import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

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
        from alembic.config import Config

        from alembic import command

        cfg = Config("alembic.ini")
        cfg.set_main_option("script_location", "alembic")
        command.upgrade(cfg, "head")
        log.info("init_db: alembic upgrade head completed")
    except Exception as e:
        log.error("init_db: alembic upgrade head failed: %s", e)
        raise


def init_db() -> None:
    """
    Initialize database.

    Policy:
      - Non-SQLite (e.g. Postgres):
          * When WEBAPP_AUTO_MIGRATE=true, run `alembic upgrade head` on startup
            so the schema is current with the running image.
          * Otherwise, rely on migrations being applied out-of-band.
      - SQLite:
          * When WEBAPP_SQLITE_USE_ALEMBIC=true (default), run `alembic upgrade head`
            here (no bootstrap).
          * When WEBAPP_SQLITE_BOOTSTRAP=true (escape hatch), apply minimal bootstrap
            and enforce critical invariants on crawls and products.
    """
    dialect = db_pool.engine.dialect.name

    if dialect != "sqlite":
        if _env_bool("WEBAPP_AUTO_MIGRATE", False):
            _run_alembic_upgrade()
        else:
            log.info("init_db: dialect=%s, path=manual_migrate", dialect)
        return

    use_alembic = _env_bool("WEBAPP_SQLITE_USE_ALEMBIC", True)
    do_bootstrap = _env_bool("WEBAPP_SQLITE_BOOTSTRAP", False)
    if use_alembic and not do_bootstrap:
        _run_alembic_upgrade()
        log.info("init_db: dialect=sqlite, path=alembic_preferred (no bootstrap)")
        return

    log.info("init_db: dialect=sqlite, path=bootstrap_emergency")

    # Create any missing tables defined in SQLAlchemy models (does not alter existing tables)
    Base.metadata.create_all(bind=db_pool.engine)

    # Helpers scoped to init to avoid polluting module namespace
    def _column_exists(conn, table: str, column: str) -> bool:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
        return any((len(r) > 1 and r[1] == column) for r in rows)

    def _table_exists(conn, table: str) -> bool:
        q = "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
        res = conn.exec_driver_sql(q, {"t": table}).first()
        return bool(res)

    def _ensure_users_table(conn) -> None:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                provider TEXT NOT NULL DEFAULT 'google',
                provider_id TEXT NOT NULL,
                name TEXT,
                avatar_url TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, provider_id)
            )
            """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_users_provider ON users(provider, provider_id)"
        )

    def _ensure_auth_sessions_table(conn) -> None:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_session_id ON auth_sessions(session_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions(user_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at ON auth_sessions(expires_at)"
        )

    def _ensure_oauth_states_table(conn) -> None:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                id TEXT PRIMARY KEY,
                sid TEXT,
                state TEXT NOT NULL,
                next_path TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_oauth_states_expires_at ON oauth_states(expires_at)"
        )

    def _ensure_crawls_columns_and_indexes(conn) -> None:
        # Ensure columns exist on crawls
        if not _column_exists(conn, "crawls", "user_id"):
            conn.exec_driver_sql("ALTER TABLE crawls ADD COLUMN user_id TEXT")
        if not _column_exists(conn, "crawls", "crawl_params"):
            conn.exec_driver_sql("ALTER TABLE crawls ADD COLUMN crawl_params TEXT")
        # Indexes (names align with SQLAlchemy model where possible)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_crawls_user_id ON crawls(user_id)"
        )

    def _unique_indexes_columns(conn, table: str) -> dict:
        rows = conn.exec_driver_sql(f"PRAGMA index_list({table})").all()
        uniques = [r for r in rows if len(r) >= 3 and bool(r[2])]
        result = {}
        for r in uniques:
            idx_name = r[1]
            cols = conn.exec_driver_sql(f"PRAGMA index_info({idx_name})").all()
            ordered_cols = [c[2] for c in cols if len(c) >= 3]
            result[idx_name] = ordered_cols
        return result

    def _has_unique_index(conn, table: str, columns: list) -> bool:
        try:
            idx_cols = _unique_indexes_columns(conn, table)
            for cols in idx_cols.values():
                if cols == columns:
                    return True
            return False
        except Exception:
            return False

    def _has_fk(conn, table: str, from_col: str, ref_table: str, to_col: str) -> bool:
        try:
            fks = conn.exec_driver_sql(f"PRAGMA foreign_key_list({table})").all()
            # pragma foreign_key_list columns (positional): id, seq, table, from, to, on_update, on_delete, match
            for r in fks:
                if (
                    len(r) >= 5
                    and str(r[2]) == ref_table
                    and str(r[3]) == from_col
                    and str(r[4]) == to_col
                ):
                    return True
            return False
        except Exception:
            return False

    # Execute emergency migration with fail-fast semantics
    try:
        with db_pool.engine.begin() as conn:
            # Validate that base tables exist before altering (crawls is required by app)
            if not _table_exists(conn, "crawls"):
                raise RuntimeError(
                    "Required table 'crawls' is missing. Database is incompatible."
                )
            # Create new tables if missing
            _ensure_users_table(conn)
            _ensure_auth_sessions_table(conn)
            _ensure_oauth_states_table(conn)
            # Add/ensure new columns + indexes on crawls
            _ensure_crawls_columns_and_indexes(conn)

            # Constraint validation for legacy installations (crawls)
            if not _has_unique_index(
                conn, "crawls", ["visibility", "domain", "path", "query"]
            ):
                raise RuntimeError(
                    "Missing unique index on crawls(visibility, domain, path, query). "
                    "This ensures deduplication for public entries. A legacy database likely needs a table rebuild."
                )
            if not _has_unique_index(conn, "crawls", ["key"]):
                raise RuntimeError(
                    "Missing unique index on crawls(key). This ensures uniqueness of public keys. "
                    "A legacy database likely needs a table rebuild."
                )
            if not _has_fk(conn, "crawls", "user_id", "users", "id"):
                raise RuntimeError(
                    "Missing foreign key on crawls.user_id -> users(id). "
                    "A legacy database likely needs a table rebuild to attach FK constraints."
                )

            # New: products invariants (unique and FK)
            if _table_exists(conn, "products"):
                if not _has_unique_index(conn, "products", ["user_id", "name"]):
                    # Attempt to create the named unique index when safe
                    conn.exec_driver_sql(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_products_user_name ON products(user_id, name)"
                    )
                    # Re-validate
                    if not _has_unique_index(conn, "products", ["user_id", "name"]):
                        raise RuntimeError(
                            "Missing unique index on products(user_id, name) post-repair."
                        )
                if not _has_fk(conn, "products", "user_id", "users", "id"):
                    # Cannot add FK without table rebuild; fail with guidance
                    raise RuntimeError(
                        "Missing foreign key on products.user_id -> users(id). "
                        "Run Alembic migrations or rebuild the SQLite DB."
                    )

            # Basic validation
            for tbl in ("users", "auth_sessions", "crawls"):
                if not _table_exists(conn, tbl):
                    raise RuntimeError(
                        f"Required table '{tbl}' is missing post-migration"
                    )

            # Required columns
            required_crawls_cols = ("user_id", "crawl_params")
            for c in required_crawls_cols:
                if not _column_exists(conn, "crawls", c):
                    raise RuntimeError(
                        f"Column '{c}' missing on 'crawls' post-migration"
                    )

    except Exception as exc:
        # Fail fast with clear message; let startup abort
        raise RuntimeError(f"Emergency SQLite bootstrap failed: {exc}") from exc


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
