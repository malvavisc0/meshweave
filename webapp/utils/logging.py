import json
import logging
import os
import sys
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

DEFAULT_LOG_LEVEL = os.getenv("WEBAPP_LOG_LEVEL", "INFO").upper()


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter to avoid extra deps."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a LogRecord as a JSON string.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: JSON-encoded string for structured logging.
        """
        base: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach standard extras if present
        for attr in (
            "request_id",
            "path",
            "method",
            "ip",
            "user_agent",
            "actor",
            "event",
            "error_code",
        ):
            if hasattr(record, attr):
                base[attr] = getattr(record, attr)  # type: ignore[attr-defined]

        # If record has an 'extra' dict, merge it
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            # prevent overriding core keys unless explicit
            for k, v in extra.items():
                if k not in base or k in ("event", "error_code"):
                    base[k] = v

        # Stack trace on errors if present
        if record.exc_info:
            base["exc"] = "".join(traceback.format_exception(*record.exc_info))  # type: ignore[arg-type]

        return json.dumps(base, ensure_ascii=False)


def init_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """Initialize root logger with JSON formatter."""
    root = logging.getLogger()
    # Avoid duplicating handlers on reload
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    try:
        root.setLevel(getattr(logging, level, logging.INFO))
    except Exception:
        root.setLevel(logging.INFO)

    # Quiet overly noisy server loggers (agnostic: Uvicorn/Gunicorn/Hypercorn) if present
    noisy_loggers = (
        "uvicorn.error",
        "uvicorn.access",
        "gunicorn.error",
        "gunicorn.access",
        "hypercorn.error",
        "hypercorn.access",
    )
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.INFO)


def get_audit_logger() -> logging.Logger:
    """Return the configured audit logger.

    Returns:
        logging.Logger: Logger named 'audit' with INFO level and propagation enabled.
    """
    logger = logging.getLogger("audit")
    logger.propagate = True
    logger.setLevel(logging.INFO)
    return logger


def _redact_sensitive(value):
    """Redact sensitive values in dicts/lists/strings.

    Removes or masks values for keys like emails, tokens, cookies, and auth headers.

    Args:
        value: Arbitrary Python object (dict/list/str/scalar) to sanitize.

    Returns:
        Any: A sanitized copy with sensitive fields redacted where applicable.
    """
    SENSITIVE_KEYS = {
        "email",
        "authorization",
        "set-cookie",
        "cookie",
        "cookies",
        "id_token",
        "access_token",
        "refresh_token",
        "oauth_token",
        "token",
        "secret",
    }

    def _sanitize(obj):
        """Recursively sanitize nested containers and scalars.

        Args:
            obj: Any JSON-like structure (dict, list, scalar).

        Returns:
            Any: Sanitized structure with sensitive fields masked.
        """
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in SENSITIVE_KEYS:
                    out[k] = "***REDACTED***"
                else:
                    out[k] = _sanitize(v)
            return out
        if isinstance(obj, list):
            return [_sanitize(x) for x in obj]
        # leave scalars as-is
        return obj

    return _sanitize(value)


def log_audit(
    event: str,
    *,
    request: Request | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a structured audit log event."""
    logger = get_audit_logger()
    extra: dict[str, Any] = {"event": event}
    if request is not None:
        try:
            extra.update(
                {
                    "request_id": getattr(request.state, "request_id", None),
                    "path": str(request.url.path),
                    "method": request.method,
                    "ip": _client_ip(request),
                    "user_agent": request.headers.get("user-agent"),
                }
            )
        except Exception:
            pass
    if fields:
        extra.update(fields)
    # Redact sensitive information before logging
    extra = cast(dict[str, Any], _redact_sensitive(extra))
    logger.log(level, event, extra={"extra": extra})


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP extraction.

    Args:
        request (Request): Incoming request.

    Returns:
        Optional[str]: First IP from X-Forwarded-For or client host; None if unavailable.
    """
    # Mirrors minimal logic; full trust-proxy logic is elsewhere
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign a per-request UUID and expose via header."""

    async def dispatch(self, request: Request, call_next):
        """Assign request_id and include it in response header.

        Args:
            request (Request): Incoming request.
            call_next: ASGI call-next.

        Returns:
            Response: Downstream response with X-Request-ID header.
        """
        rid = str(uuid.uuid4())
        request.state.request_id = rid
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
