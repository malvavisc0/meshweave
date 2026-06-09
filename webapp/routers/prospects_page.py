import os
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.infra import templates
from webapp.utils.auth import require_auth
from webapp.utils.config import _env_bool
from webapp.utils.security import _make_csrf_token
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/prospects", response_class=HTMLResponse)
async def prospects_page(request: Request):
    """Prospects management page (UI mirrors Products page patterns)."""
    await require_auth(request)

    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"Prospects — {site_name}"
    meta_description = "Manage your prospects and contacts."

    # CSRF token for forms (consistent with Products page)
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    session_id = request.cookies.get(cookie_name)
    new_session = False
    if _env_bool("WEBAPP_CSRF_ENABLED", False) and not session_id:
        session_id = str(uuid.uuid4())
        new_session = True
    csrf_token = (
        _make_csrf_token(session_id)
        if (_env_bool("WEBAPP_CSRF_ENABLED", False) and session_id)
        else ""
    )

    resp = templates.TemplateResponse(
        request,
        "prospects.html",
        {
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": _abs_url(request, "/prospects"),
            "csrf_token": csrf_token,
        },
    )

    if new_session and session_id:
        cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
        session_ttl = int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
        resp.set_cookie(
            key=cookie_name,
            value=str(session_id),
            max_age=session_ttl,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )
    return resp
