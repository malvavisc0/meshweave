import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from starlette import status

from webapp.db import get_session
from webapp.models import AuthSession, OAuthState, User
from webapp.utils.auth import (
    create_auth_session,
    get_auth_cookie_value,
    get_current_user,
    require_auth,
    sanitize_next,
    set_auth_cookie,
)
from webapp.utils.config import _env_bool
from webapp.utils.metrics import auth_attempts, rate_limit_hits
from webapp.utils.security import _verify_csrf_token
from webapp.utils.url import _get_base_url

router = APIRouter()
logger = logging.getLogger(__name__)

# Google OIDC endpoints (avoid discovery to keep dependencies minimal)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _oauth_state_ttl() -> int:
    """Get TTL for OAuth state records in seconds.

    Returns:
        int: Number of seconds a state value remains valid. Defaults to 600 if not set.
    """
    try:
        return int(os.getenv("OAUTH_STATE_TTL", "600"))
    except Exception:
        return 600


def _login_rate_limit_max() -> int:
    """Maximum number of login attempts allowed within the rate-limit window.

    Returns:
        int: Attempt limit. Defaults to 5 if not set.
    """
    try:
        return int(os.getenv("LOGIN_RATE_LIMIT_MAX", "5"))
    except Exception:
        return 5


def _login_rate_limit_window_sec() -> int:
    """Sliding window size for login rate limiting in seconds.

    Returns:
        int: Window size in seconds. Defaults to 900 (15 minutes) if not set.
    """
    try:
        return int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SEC", "900"))
    except Exception:
        return 900


def _sid_cookie_name() -> str:
    """Return the name of the session cookie.

    Returns:
        str: Cookie name used to store anonymous/session identifier. Defaults to 'sid'.
    """
    return os.getenv("WEBAPP_COOKIE_NAME", "sid")


def _sid_cookie_secure() -> bool:
    """Whether the session cookie should be marked as 'Secure'.

    Returns:
        bool: True if the cookie should be sent only over HTTPS.
    """
    return _env_bool("WEBAPP_COOKIE_SECURE", False)


def _sid_cookie_ttl() -> int:
    """Session cookie TTL in seconds.

    Returns:
        int: Max-age for the session cookie. Defaults to 43200 (12 hours) if not set.
    """
    try:
        return int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
    except Exception:
        return 43200


def _client_id() -> str:
    """Fetch configured OAuth client ID.

    Returns:
        str: The OAuth client ID.

    Raises:
        RuntimeError: If OAUTH_CLIENT_ID is not configured.
    """
    cid = os.getenv("OAUTH_CLIENT_ID") or ""
    if not cid:
        raise RuntimeError("OAUTH_CLIENT_ID is not configured")
    return cid


def _client_secret() -> str:
    """Fetch configured OAuth client secret.

    Returns:
        str: The OAuth client secret.

    Raises:
        RuntimeError: If OAUTH_CLIENT_SECRET is not configured.
    """
    cs = os.getenv("OAUTH_CLIENT_SECRET") or ""
    if not cs:
        raise RuntimeError("OAUTH_CLIENT_SECRET is not configured")
    return cs


def _allowed_domains() -> set | None:
    """Domains allowed to authenticate, if enforced.

    Returns:
        Optional[set]: Set of lowercased domain names parsed from OAUTH_ALLOWED_DOMAINS,
            or None if the environment variable is not set or empty.
    """
    v = os.getenv("OAUTH_ALLOWED_DOMAINS")
    if not v:
        return None
    vals = [s.strip().lower() for s in v.split(",") if s.strip()]
    return set(vals) if vals else None


async def _rate_limit_login(sid: str | None) -> None:
    """Rate-limit login attempts using oauth_states records.

    Args:
        sid (Optional[str]): Anonymous session identifier used to correlate attempts.

    Raises:
        HTTPException: 429 Too Many Requests when the limit is exceeded.
    """
    max_attempts = _login_rate_limit_max()
    window_sec = _login_rate_limit_window_sec()
    if max_attempts <= 0:
        return
    now = datetime.now(UTC)
    window_start = now - timedelta(seconds=window_sec)
    # Count recent oauth_states for this sid
    with get_session() as s:
        count_query = s.query(func.count(OAuthState.id)).filter(
            OAuthState.created_at >= window_start
        )
        if sid:
            count_query = count_query.filter(OAuthState.sid == sid)
        count = count_query.scalar() or 0
        if count >= max_attempts:
            try:
                rate_limit_hits.labels("login").inc()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
            )


def _build_redirect_uri(request: Request) -> str:
    """Build the absolute redirect URI for the OAuth callback endpoint.

    Args:
        request (Request): Incoming request used to derive the base URL if SITE_BASE_URL
            is not set.

    Returns:
        str: Absolute callback URL (e.g., https://example.com/auth/callback).
    """
    base = os.getenv("SITE_BASE_URL") or _get_base_url(request)
    base = base.rstrip("/")
    return f"{base}/auth/callback"


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, provider: str = "google", next: str | None = None):
    """Start Google OAuth login, store server-side state, and redirect to the provider.

    Args:
        request (Request): Incoming request used for cookie and base URL resolution.
        provider (str): OAuth provider identifier. Only "google" is supported.
        next (Optional[str]): Optional path to redirect to after authentication.

    Returns:
        RedirectResponse: 302 redirect to the Google OAuth authorization URL.

    Raises:
        HTTPException: 503 if auth is disabled, 400 if provider is unsupported.
    """
    if not _env_bool("WEBAPP_AUTH_ENABLED", True):
        raise HTTPException(status_code=503, detail="Auth disabled")

    if provider != "google":
        raise HTTPException(status_code=400, detail="Unsupported provider")

    next_path = sanitize_next(next)

    # Ensure we have an anonymous sid cookie for CSRF/rate-limit correlation
    sid_cookie_name = _sid_cookie_name()
    sid = request.cookies.get(sid_cookie_name)
    set_sid = False
    if not sid:
        sid = str(uuid.uuid4())
        set_sid = True

    # Rate limit login attempts
    await _rate_limit_login(sid)

    # Store state server-side
    state = secrets.token_urlsafe(16)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=_oauth_state_ttl())
    with get_session() as s:
        s.add(
            OAuthState(
                sid=sid,
                state=state,
                next_path=next_path,
                created_at=now,
                expires_at=expires_at,
            )
        )

    # Build auth URL
    redirect_uri = _build_redirect_uri(request)
    params = {
        "client_id": _client_id(),
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": state,
        # UX niceties:
        "prompt": "select_account",
        "include_granted_scopes": "true",
        "access_type": "online",
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{httpx.QueryParams(params)}"

    resp = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    if set_sid:
        resp.set_cookie(
            key=sid_cookie_name,
            value=sid,
            max_age=_sid_cookie_ttl(),
            httponly=True,
            samesite="lax",
            secure=_sid_cookie_secure(),
            path="/",
        )
    return resp


def _auth_failure_redirect() -> RedirectResponse:
    """Generic OAuth failure redirect that does not leak provider details."""
    try:
        auth_attempts.labels("google", "failure").inc()
    except Exception:
        pass
    return RedirectResponse(url="/?error=1", status_code=status.HTTP_303_SEE_OTHER)


def _validate_oauth_state(state: str, sid: str) -> tuple[str | None, OAuthState]:
    """Validate and consume a single-use OAuth state, returning (next_path, row)."""
    with get_session() as s:
        row = (
            s.query(OAuthState)
            .filter(OAuthState.state == state, OAuthState.sid == sid)
            .first()
        )
        if not row:
            raise HTTPException(status_code=400, detail="Invalid state")
        # SQLite returns naive datetimes for DateTime(timezone=True) columns
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        next_path_val = row.next_path
        s.delete(row)

    if datetime.now(UTC) > expires_at:
        # State was already deleted above; commit happened on clean exit
        raise HTTPException(status_code=400, detail="State expired")
    return next_path_val, row


async def _exchange_google_code(code: str, redirect_uri: str) -> dict | None:
    """Exchange the auth code for verified Google user info, or None on failure."""
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            logger.warning(
                "Google OAuth token exchange failed with status %s",
                token_resp.status_code,
            )
            return None
        tok = token_resp.json()
        access_token = tok.get("access_token")
        if not access_token:
            return None

        ui_resp = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if ui_resp.status_code != 200:
            logger.warning(
                "Google OAuth user-info request failed with status %s",
                ui_resp.status_code,
            )
            return None
        data = ui_resp.json()
        return data if isinstance(data, dict) else None


def _google_user_allowed(email: str) -> bool:
    """Whether the OAuth email is allowed by the configured domain whitelist."""
    domains = _allowed_domains()
    if not domains:
        return True
    try:
        domain = email.split("@", 1)[1]
    except Exception:
        domain = ""
    return domain.lower() in domains


def _new_google_user(email: str, sub: str, name, picture) -> User:
    """Build a new Google user row.

    Args:
        email: Verified Google email address.
        sub: Google-provided subject identifier.
        name: Display name (optional).
        picture: Avatar URL (optional).

    Returns:
        User: Unsaved new user row.
    """
    return User(
        id=str(uuid.uuid4()),
        provider="google",
        provider_id=sub,
        email=email,
        name=name,
        avatar_url=picture,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _refresh_google_user_fields(user: User, email: str, name, picture) -> None:
    """Update mutable profile fields on an existing Google user."""
    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if name and not user.name:
        user.name = name
        changed = True
    if picture and user.avatar_url != picture:
        user.avatar_url = picture
        changed = True
    if changed:
        user.updated_at = datetime.now(UTC)


def _upsert_google_user(
    email: str, sub: str, name, picture
) -> tuple[User, AuthSession] | None:
    """Upsert the Google user and create an auth session, or None on failure."""
    with get_session() as s:
        user = (
            s.query(User)
            .filter(User.provider == "google", User.provider_id == sub)
            .one_or_none()
        )
        if not user:
            user = _new_google_user(email, sub, name, picture)
            s.add(user)
            s.flush()
        else:
            # Update mutable fields
            _refresh_google_user_fields(user, email, name, picture)

        # Create auth session (enforces concurrent limit)
        # Commit user upsert before creating session in a separate DB transaction
        try:
            s.commit()
        except Exception:
            # If commit fails, redirect with error
            return None
        try:
            sess = create_auth_session(user.id)
        except HTTPException:
            return None
        return user, sess


def _validated_google_profile(
    ui: dict,
) -> tuple[str, str, str | None, str | None] | None:
    """Extract and validate Google profile fields from user info.

    Args:
        ui: Verified Google userinfo payload.

    Returns:
        Optional[tuple]: (sub, email, name, picture), or None when unusable.
    """
    sub = (ui.get("sub") or "").strip()
    email = (ui.get("email") or "").strip().lower()
    email_verified = bool(ui.get("email_verified"))
    name = (ui.get("name") or "").strip() or None
    picture = (ui.get("picture") or "").strip() or None
    if not sub or not email or not email_verified:
        return None
    return sub, email, name, picture


def _auth_success_redirect(
    next_path_val: str | None, oauth_row: OAuthState, sess: AuthSession
) -> RedirectResponse:
    """Build the post-login redirect response with the auth cookie set."""
    # Redirect to requested path; set auth cookie
    next_path = sanitize_next(next_path_val if oauth_row else None)
    resp = RedirectResponse(url=next_path or "/", status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(resp, sess.session_id)
    try:
        auth_attempts.labels("google", "success").inc()
    except Exception:
        pass
    return resp


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
):
    """Handle OAuth redirect, validate state, exchange code, upsert user, and set auth cookie.

    Args:
        request (Request): Incoming request containing cookies and query params.
        state (Optional[str]): Opaque state value returned by the provider.
        code (Optional[str]): Authorization code returned by the provider.
        error (Optional[str]): Provider error indicator, if any.

    Returns:
        RedirectResponse: 303 redirect to the next_path or home on success; to "/" with
        error indicator on failure.

    Raises:
        HTTPException: 400 for invalid or expired state or when required parameters are missing.
    """
    if error:
        logger.warning("Google OAuth callback returned provider error: %s", error)
        return _auth_failure_redirect()

    if not state or not code:
        raise HTTPException(status_code=400, detail="Invalid OAuth response")

    # Validate server-side state; single-use
    sid = request.cookies.get(_sid_cookie_name()) or ""
    next_path_val, oauth_row = _validate_oauth_state(state, sid)

    # Exchange code for tokens
    redirect_uri = _build_redirect_uri(request)
    ui = await _exchange_google_code(code, redirect_uri)

    profile = _validated_google_profile(ui) if ui else None
    if profile is None:
        logger.warning("Google OAuth callback did not yield a verified profile")
        return _auth_failure_redirect()
    sub, email, name, picture = profile

    if not _google_user_allowed(email):
        logger.warning("Google OAuth account was rejected by the domain allowlist")
        return _auth_failure_redirect()

    upsert = _upsert_google_user(email, sub, name, picture)
    if upsert is None:
        logger.warning("Google OAuth user or session creation failed")
        return _auth_failure_redirect()
    user, sess = upsert

    return _auth_success_redirect(next_path_val, oauth_row, sess)


@router.post("/logout")
async def logout(request: Request, csrf_token: str | None = Form(None)):
    """Logout current session.

    For authenticated users, CSRF is not required to allow logout even if session state is stale.
    For anonymous users, CSRF is required to prevent abuse.

    Args:
        request (Request): Incoming request used to read cookies.
        csrf_token (Optional[str]): CSRF token bound to the anonymous sid cookie.

    Returns:
        RedirectResponse: 303 redirect to the home page.

    Raises:
        HTTPException: 403 when CSRF validation fails for anonymous users.
    """
    # Check if user is authenticated
    user = await get_current_user(request)
    if user:
        # Authenticated: allow logout without CSRF check
        pass
    else:
        # Anonymous: require CSRF
        sid_cookie = request.cookies.get(_sid_cookie_name()) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, sid_cookie, max_age_seconds=max_age):
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    # Remove current auth session if present
    from webapp.utils.auth import clear_auth_cookie  # local import to avoid cycle

    sess_id = get_auth_cookie_value(request)
    if sess_id:
        from webapp.utils.auth import destroy_auth_session_by_id

        destroy_auth_session_by_id(sess_id)

    resp = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookie(resp)
    return resp


@router.post("/logout-all")
async def logout_all(request: Request, csrf_token: str | None = Form(None)):
    """Logout all sessions for current user (CSRF required).

    Args:
        request (Request): Incoming request used to read cookies and determine user.
        csrf_token (Optional[str]): CSRF token bound to the anonymous sid cookie.

    Returns:
        RedirectResponse: 303 redirect to the home page.

    Raises:
        HTTPException: 403 when CSRF validation fails.
    """
    # CSRF (always enforced)
    sid_cookie = request.cookies.get(_sid_cookie_name()) or ""
    max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
    if not _verify_csrf_token(csrf_token, sid_cookie, max_age_seconds=max_age):
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    user = await require_auth(request)
    from webapp.utils.auth import clear_auth_cookie, destroy_all_sessions_for_user

    destroy_all_sessions_for_user(user.id)
    resp = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookie(resp)
    return resp
