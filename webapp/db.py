import logging
import os
from contextlib import contextmanager
from typing import Iterator

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

if DATABASE_URL:
    # Use external database (e.g., Postgres) when configured
    engine = create_engine(
        DATABASE_URL,
        future=True,
        pool_pre_ping=True,
    )
    if engine.dialect.name == "sqlite":
        log.warning(
            "DATABASE_URL is set but SQLite dialect detected (%s). Verify configuration.",
            DATABASE_URL,
        )
else:
    # Default to SQLite
    engine = create_engine(
        f"sqlite:///{SQLITE_PATH}",
        connect_args={
            "check_same_thread": False,
            "timeout": 30.0,
        },  # needed for SQLite with threads
        future=True,
    )


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


if engine.dialect.name == "sqlite":
    event.listen(engine, "connect", _set_sqlite_pragma)


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
)


def init_db() -> None:
    """
    Initialize database for local/dev when using SQLite.

    Policy:
      - Non-SQLite: rely exclusively on Alembic migrations (no-op here).
      - SQLite:
          * When WEBAPP_SQLITE_USE_ALEMBIC=true (default), do not perform bootstrap here;
            expect Alembic to run at startup (see app.lifespan auto-migrate).
          * When WEBAPP_SQLITE_BOOTSTRAP=true (escape hatch), apply minimal bootstrap and
            enforce critical invariants on crawls and products.
    """
    dialect = engine.dialect.name
    if dialect != "sqlite":
        log.info("init_db: dialect=%s, path=alembic_only", dialect)
        return

    use_alembic = os.getenv("WEBAPP_SQLITE_USE_ALEMBIC", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    do_bootstrap = os.getenv("WEBAPP_SQLITE_BOOTSTRAP", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if use_alembic and not do_bootstrap:
        log.info("init_db: dialect=sqlite, path=alembic_preferred (no bootstrap)")
        return

    log.info("init_db: dialect=sqlite, path=bootstrap_emergency")

    # Create any missing tables defined in SQLAlchemy models (does not alter existing tables)
    Base.metadata.create_all(bind=engine)

    # Helpers scoped to init to avoid polluting module namespace
    def _column_exists(conn, table: str, column: str) -> bool:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
        return any((len(r) > 1 and r[1] == column) for r in rows)

    def _table_exists(conn, table: str) -> bool:
        q = "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
        res = conn.exec_driver_sql(q, {"t": table}).first()
        return bool(res)

    def _ensure_users_table(conn) -> None:
        conn.exec_driver_sql(
            """
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
            """
        )
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_users_provider ON users(provider, provider_id)"
        )

    def _ensure_auth_sessions_table(conn) -> None:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
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
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                id TEXT PRIMARY KEY,
                sid TEXT,
                state TEXT NOT NULL,
                next_path TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_oauth_states_expires_at ON oauth_states(expires_at)"
        )

    def _ensure_crawls_columns_and_indexes(conn) -> None:
        # Ensure columns exist on crawls
        if not _column_exists(conn, "crawls", "user_id"):
            conn.exec_driver_sql("ALTER TABLE crawls ADD COLUMN user_id TEXT")
        if not _column_exists(conn, "crawls", "scope"):
            conn.exec_driver_sql(
                "ALTER TABLE crawls ADD COLUMN scope TEXT DEFAULT 'page'"
            )
        if not _column_exists(conn, "crawls", "limits_json"):
            conn.exec_driver_sql("ALTER TABLE crawls ADD COLUMN limits_json TEXT")
        # Indexes (names align with SQLAlchemy model where possible)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_crawls_user_id ON crawls(user_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_crawls_scope ON crawls(scope)"
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
        with engine.begin() as conn:
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
            required_crawls_cols = ("user_id", "scope", "limits_json")
            for c in required_crawls_cols:
                if not _column_exists(conn, "crawls", c):
                    raise RuntimeError(f"Column '{c}' missing on 'crawls' post-migration")

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
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
