import os
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette import status
from starlette.middleware.base import BaseHTTPMiddleware

from webapp.db import get_session
from webapp.models import AuthSession, Crawl, User
from webapp.utils.config import _env_bool
from webapp.utils.metrics import active_sessions


# -----------------------------
# Config helpers
# -----------------------------
def _auth_enabled() -> bool:
    """Whether auth features are enabled.

    Returns:
        bool: True if WEBAPP_AUTH_ENABLED is truthy, else False.
    """
    return _env_bool("WEBAPP_AUTH_ENABLED", True)


def _cookie_secure() -> bool:
    """Whether the auth cookie should be marked Secure.

    Returns:
        bool: True if WEBAPP_AUTH_COOKIE_SECURE is truthy.
    """
    return _env_bool("WEBAPP_AUTH_COOKIE_SECURE", False)


def _auth_cookie_base_name() -> str:
    """Base cookie name before any security prefixing.

    Returns:
        str: Base cookie name from WEBAPP_AUTH_COOKIE_NAME, default 'auth_sid'.
    """
    # Base name; when secure, we will add "__Secure-" prefix on set
    return os.getenv("WEBAPP_AUTH_COOKIE_NAME", "auth_sid")


def _resolve_cookie_name_for_set() -> str:
    """Resolve the cookie name to use when setting the auth cookie.

    Returns:
        str: '__Secure-' + base when secure cookies are enabled; otherwise the base name.
    """
    base = _auth_cookie_base_name()
    if _cookie_secure():
        # Per spec, __Secure- prefix is only valid when Secure flag is set
        if not base.startswith("__Secure-"):
            return "__Secure-" + base
    return base


def _resolve_cookie_names_for_read() -> tuple[str, str]:
    """Return candidate cookie names for reading.

    Returns:
        Tuple[str, str]: (secure-prefixed name, base name). Clients may have either.
    """
    # Try secure-prefixed then base
    base = _auth_cookie_base_name()
    secure_name = base if base.startswith("__Secure-") else "__Secure-" + base
    return secure_name, base


def _auth_ttl_seconds() -> int:
    """Sliding TTL (seconds) for the auth cookie/session.

    Returns:
        int: Default 86400 (24h) when WEBAPP_AUTH_TTL_SECONDS is unset/invalid.
    """
    try:
        return int(os.getenv("WEBAPP_AUTH_TTL_SECONDS", "86400"))  # 24h default
    except Exception:
        return 86400


def _auth_abs_max_seconds() -> int:
    """Absolute max lifetime (seconds) for a session from creation.

    Returns:
        int: Default 604800 (7 days) when WEBAPP_AUTH_ABS_MAX_SECONDS is unset/invalid.
    """
    try:
        return int(os.getenv("WEBAPP_AUTH_ABS_MAX_SECONDS", "604800"))  # 7d default
    except Exception:
        return 604800


def _max_user_sessions() -> int:
    """Maximum concurrent auth sessions allowed per user.

    Returns:
        int: Limit from AUTH_USER_MAX_SESSIONS, default 5.
    """
    try:
        return int(os.getenv("AUTH_USER_MAX_SESSIONS", "5"))
    except Exception:
        return 5


# -----------------------------
# Public helpers
# -----------------------------
def get_auth_cookie_value(request: Request) -> str | None:
    """Read the auth cookie value using secure name first then base.

    Args:
        request (Request): Incoming request object providing access to cookies.

    Returns:
        Optional[str]: The auth session_id value if present; otherwise None.
    """
    if not getattr(request, "cookies", None):
        return None
    secure_name, base = _resolve_cookie_names_for_read()
    return request.cookies.get(secure_name) or request.cookies.get(base)


def set_auth_cookie(resp: Response, session_id: str) -> None:
    """Set the auth cookie with appropriate flags and name.

    Args:
        resp (Response): Outgoing response to set the cookie on.
        session_id (str): Public session identifier to store in the cookie.

    Returns:
        None
    """
    name = _resolve_cookie_name_for_set()
    ttl = _auth_ttl_seconds()
    resp.set_cookie(
        key=name,
        value=session_id,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


def clear_auth_cookie(resp: Response) -> None:
    """Clear the auth cookie on client.

    Args:
        resp (Response): Outgoing response to clear the cookie on.

    Returns:
        None
    """
    name = _resolve_cookie_name_for_set()
    resp.delete_cookie(key=name, path="/")


def sanitize_next(next_path: str | None) -> str:
    """Allow only same-origin, relative paths. Default to '/'.

    Args:
        next_path (Optional[str]): Candidate redirect path. May be None or invalid.

    Returns:
        str: A safe, normalized path starting with '/', or '/' when invalid.
    """
    p = (next_path or "/").strip()
    # Disallow absolute URLs or protocol-relative
    if p.startswith("http://") or p.startswith("https://") or p.startswith("//"):
        return "/"
    if not p.startswith("/"):
        return "/"
    # Optionally normalize to avoid path traversal issues (keep simple)
    return p or "/"


async def get_current_user(request: Request) -> User | None:
    """Return the current user from an unexpired auth session, else None.

    Args:
        request (Request): Incoming request used to read the auth cookie.

    Returns:
        Optional[User]: The authenticated user if a valid, unexpired session exists;
        otherwise None.
    """
    if not _auth_enabled():
        return None
    sid_cookie = get_auth_cookie_value(request)
    if not sid_cookie:
        return None
    now = datetime.now(UTC)
    with get_session() as s:
        sess: AuthSession | None = (
            s.query(AuthSession)
            .filter(AuthSession.session_id == sid_cookie)
            .one_or_none()
        )
        if not sess:
            return None
        # Basic expiration check
        if sess.expires_at and isinstance(sess.expires_at, datetime):
            if now >= sess.expires_at:
                return None
        user: User | None = s.get(User, sess.user_id)
        return user


async def require_auth(request: Request) -> User:
    """Require an authenticated user, else raise 401 Unauthorized.

    Args:
        request (Request): Incoming request.

    Returns:
        User: The authenticated user.

    Raises:
        HTTPException: 401 when authentication is disabled or no valid session exists.
    """
    if not _auth_enabled():
        # Enforce locked-down behavior when misconfigured
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    user = await get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return user


async def require_ownership(request: Request, crawl_id: str) -> Crawl:
    """Require auth and ownership for private crawls addressed by UUID.

    Args:
        request (Request): Incoming request.
        crawl_id (str): UUID of the private crawl row.

    Returns:
        Crawl: The owned crawl row.

    Raises:
        HTTPException: 401 when unauthenticated, 404 when not found,
            403 when the row is not owned by the current user.
    """
    from sqlalchemy.orm import joinedload

    user = await require_auth(request)
    with get_session() as s:
        row: Crawl | None = s.get(
            Crawl,
            crawl_id,
            options=[joinedload(Crawl.score_snapshot)],
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
            )
        if not row.user_id or row.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        return row


# -----------------------------
# Session lifecycle (Phase 2)
# -----------------------------
def _now() -> datetime:
    """Current UTC datetime.

    Returns:
        datetime: Now in UTC.
    """
    return datetime.now(UTC)


def create_auth_session(user_id: str) -> AuthSession:
    """Create a new auth session for a user, enforcing concurrent session limits.

    Uses a single transaction with a write lock (BEGIN IMMEDIATE) to make the
    count+insert sequence atomic under SQLite, preventing races that exceed the
    configured max concurrent sessions.

    Args:
        user_id (str): UUID of the user to create a session for.

    Returns:
        AuthSession: The newly created auth session row.

    Raises:
        HTTPException: 429 when the user already has the maximum number of active sessions.
    """
    delays = [0.05, 0.15, 0.3]  # incremental backoff in seconds
    last_exc: Exception | None = None

    for i, delay in enumerate(delays):
        now = _now()
        ttl = _auth_ttl_seconds()
        abs_max = _auth_abs_max_seconds()
        try:
            with get_session() as s:
                # Acquire a pre-emptive write lock only on SQLite
                try:
                    bind = getattr(s, "bind", None)
                    if bind is not None and getattr(bind, "dialect", None) is not None:
                        if bind.dialect.name == "sqlite":
                            s.execute(text("BEGIN IMMEDIATE"))
                except Exception:
                    # If lock acquisition fails, proceed; SQLite often serializes writes implicitly
                    pass

                # Enforce concurrent sessions atomically within this transaction
                active_count = (
                    s.query(AuthSession)
                    .filter(
                        AuthSession.user_id == user_id, AuthSession.expires_at > now
                    )
                    .count()
                )
                if active_count >= _max_user_sessions():
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many active sessions",
                    )

                sess = AuthSession(
                    id=secrets.token_urlsafe(16),
                    user_id=user_id,
                    session_id=secrets.token_urlsafe(32),
                    created_at=now,
                    last_activity=now,
                    expires_at=now + timedelta(seconds=min(ttl, abs_max)),
                )
                s.add(sess)
                s.flush()
                try:
                    active_sessions.inc()
                except Exception:
                    pass
                return sess
        except OperationalError as e:
            # Retry on transient SQLite write contention
            last_exc = e
            msg = str(e).lower()
            if ("database is locked" in msg or "database is busy" in msg) and i < len(
                delays
            ) - 1:
                time.sleep(delay)
                continue
            raise

    # If all retries failed, re-raise the last OperationalError if present
    if last_exc:
        raise last_exc
    # Defensive fallback
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Unable to create auth session",
    )


def destroy_auth_session_by_id(session_id: str) -> None:
    """Delete a single auth session by its public session_id.

    Args:
        session_id (str): Public session identifier stored in the cookie.

    Returns:
        None
    """
    with get_session() as s:
        deleted = (
            s.query(AuthSession).filter(AuthSession.session_id == session_id).delete()
        )
    if deleted:
        try:
            active_sessions.dec()
        except Exception:
            pass


def destroy_all_sessions_for_user(user_id: str) -> None:
    """Delete all auth sessions for a given user.

    Args:
        user_id (str): User UUID.

    Returns:
        None
    """
    with get_session() as s:
        # Determine how many we will delete to adjust gauge conservatively
        count = s.query(AuthSession).filter(AuthSession.user_id == user_id).count()
        s.query(AuthSession).filter(AuthSession.user_id == user_id).delete()
    if count:
        try:
            active_sessions.dec(count)
        except Exception:
            pass


def slide_session_expiry(session: AuthSession) -> AuthSession:
    """Update session last_activity and sliding TTL within absolute cap.

    Args:
        session (AuthSession): Session to update.

    Returns:
        AuthSession: The session instance with updated last_activity and expires_at,
        after also persisting changes to the database.
    """
    now = _now()
    ttl = _auth_ttl_seconds()
    abs_max = _auth_abs_max_seconds()
    created = session.created_at or now
    abs_cap = created + timedelta(seconds=abs_max)
    new_exp = min(abs_cap, now + timedelta(seconds=ttl))
    session.last_activity = now
    session.expires_at = new_exp
    with get_session() as s:
        db_sess = (
            s.query(AuthSession).filter(AuthSession.id == session.id).one_or_none()
        )
        if db_sess:
            db_sess.last_activity = session.last_activity
            db_sess.expires_at = session.expires_at
    return session


# -----------------------------
# Middleware: Auth session sliding TTL and exposure of current_user
# -----------------------------
class AuthSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        """Attach current_user to request.state and slide session expiry.

        Args:
            request (Request): Incoming request carrying cookies.
            call_next: ASGI call-next function.

        Returns:
            Response: Starlette Response with updated/cleared auth cookie as needed.
        """
        user = None
        slid = False
        expired = False
        sid_cookie = get_auth_cookie_value(request)

        if sid_cookie:
            now = datetime.now(UTC)
            # Load and validate session
            with get_session() as s:
                sess = (
                    s.query(AuthSession)
                    .filter(AuthSession.session_id == sid_cookie)
                    .one_or_none()
                )
                if sess:
                    if sess.expires_at and now >= sess.expires_at:
                        # Delete expired session
                        s.query(AuthSession).filter(AuthSession.id == sess.id).delete()
                        expired = True
                    else:
                        # Reduce write frequency: only slide if last_activity is older than 60s
                        try:
                            if (
                                not getattr(sess, "last_activity", None)
                                or (now - sess.last_activity).total_seconds() >= 60
                            ):
                                slide_session_expiry(sess)
                                slid = True
                        except Exception:
                            # On any failure, skip sliding to avoid cascading errors
                            pass
                        # Always fetch user for template access
                        user = s.get(User, sess.user_id)

        # Expose current_user to request.state for templates
        try:
            request.state.current_user = user
        except Exception:
            pass

        response = await call_next(request)

        # Update/clear cookie based on session state
        if expired:
            try:
                clear_auth_cookie(response)
            except Exception:
                pass
        elif slid and sid_cookie:
            try:
                set_auth_cookie(response, sid_cookie)
            except Exception:
                pass

        return response
