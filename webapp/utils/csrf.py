from urllib.parse import urlparse

from fastapi.responses import JSONResponse, PlainTextResponse
from starlette import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from webapp.utils.config import _env_bool
from webapp.utils.logging import log_audit


def _host_of(u: str | None) -> str:
    """Extract netloc host from a URL-like string in lowercase.

    Args:
        u (Optional[str]): URL or origin string.

    Returns:
        str: Lowercased host without scheme or path, or empty string on error.
    """
    try:
        return (urlparse(u or "").netloc or "").lower()
    except Exception:
        return ""


class CSRFMiddleware(BaseHTTPMiddleware):
    """Server-side CSRF protection via Origin/Referer checks.

    Applies to state-changing methods (POST, PUT, PATCH, DELETE).
    If WEBAPP_ENFORCE_ORIGIN=false, middleware is no-op.
    """

    async def dispatch(self, request: Request, call_next):
        """Validate Origin/Referer for state-changing requests.

        Args:
            request (Request): Incoming request.
            call_next: ASGI call-next to continue the pipeline.

        Returns:
            Response: 403 Forbidden when origin/referer mismatch; otherwise downstream response.
        """
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and _env_bool(
            "WEBAPP_ENFORCE_ORIGIN", True
        ):
            host_hdr = (request.headers.get("host") or "").lower()
            origin_hdr = request.headers.get("origin")
            referer_hdr = request.headers.get("referer")

            def reject(reason: str) -> Response:
                """Return a 403 response (JSON for API paths, text otherwise).

                Args:
                    reason (str): Reason string to audit log.

                Returns:
                    Response: JSONResponse for /api/* paths; PlainTextResponse otherwise.
                """
                try:
                    log_audit(reason, request=request)
                except Exception:
                    pass
                is_api = str(request.url.path).startswith("/api")
                if is_api:
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Forbidden"},
                    )
                return PlainTextResponse(
                    status_code=status.HTTP_403_FORBIDDEN, content="Forbidden"
                )

            if origin_hdr:
                if _host_of(origin_hdr) != host_hdr:
                    return reject("origin_not_allowed_mw")
            elif referer_hdr:
                if _host_of(referer_hdr) != host_hdr:
                    return reject("referer_not_allowed_mw")

        return await call_next(request)
