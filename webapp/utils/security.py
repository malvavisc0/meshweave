import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request

from .config import _env_bool, _get_secret_key


def _hash_ip(ip: str, salt: str) -> str:
    """Hash an IP with a salt using SHA-256.

    Args:
        ip (str): IP address string.
        salt (str): Salt string.

    Returns:
        str: Hex-encoded SHA-256 digest of "ip|salt".
    """
    s = f"{ip}|{salt}".encode()
    return hashlib.sha256(s).hexdigest()


def _make_csrf_token(session_id: str) -> str:
    """Create a CSRF token for a given session id.

    The token is a concatenation of a UNIX timestamp and HMAC signature.

    Args:
        session_id (str): Session identifier.

    Returns:
        str: CSRF token in the form "ts:mac".
    """
    ts = str(int(time.time()))
    data = f"{session_id}:{ts}:submit"
    mac = hmac.new(_get_secret_key(), data.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ts}:{mac}"


def _verify_csrf_token(
    token: str | None, session_id: str, max_age_seconds: int = 7200
) -> bool:
    """Verify a CSRF token for the given session id and max age.

    Args:
        token (Optional[str]): CSRF token "ts:mac".
        session_id (str): Session identifier to bind verification.
        max_age_seconds (int): Maximum accepted token age in seconds.

    Returns:
        bool: True if valid and within age window; otherwise False.
    """
    try:
        if not token or not session_id:
            return False
        parts = token.split(":")
        if len(parts) != 2:
            return False
        ts_s, mac = parts[0], parts[1]
        ts = int(ts_s)
        if int(time.time()) - ts > int(max_age_seconds):
            return False
        expected = hmac.new(
            _get_secret_key(),
            f"{session_id}:{ts_s}:submit".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, mac)
    except Exception:
        return False


def verify_request_csrf(request: Request, csrf_token: str | None) -> None:
    """Validate a request's CSRF token when enabled; raise 403 on failure.

    Reads the session id from the configured session cookie and validates
    the submitted token against it. No-op when WEBAPP_CSRF_ENABLED is falsy.

    Args:
        request (Request): Incoming request carrying the session cookie.
        csrf_token (Optional[str]): Submitted CSRF token ("ts:mac").

    Raises:
        HTTPException: 403 when validation fails while CSRF is enabled.
    """
    if not _env_bool("WEBAPP_CSRF_ENABLED", False):
        return
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    session_id = request.cookies.get(cookie_name) or ""
    max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
    if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
