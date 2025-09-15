import os
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.config import _env_bool
from webapp.utils.logging import log_audit
from webapp.utils.security import _make_csrf_token
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Homepage with submission form and latest 10 public results.

    Renders recent public crawls (domain, path, query, title, status) and ensures
    a session cookie and CSRF token are set.
    """
    with get_session() as s:
        rows: List[Crawl] = (
            s.query(Crawl)
            .filter(Crawl.visibility == "public")
            .order_by(Crawl.updated_at.desc())
            .limit(10)
            .all()
        )

    items = []
    for r in rows:
        title = ""
        try:
            if r.payload_json:
                import json

                payload = json.loads(r.payload_json)
                title = (payload.get("page") or {}).get("title") or ""
        except Exception:
            title = ""
        items.append(
            {
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "key": r.key,
                "title": title,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
                "status": r.status,
            }
        )

    # Ensure session cookie and CSRF token
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    session_id = request.cookies.get(cookie_name)
    new_session = False
    if not session_id:
        session_id = str(uuid.uuid4())
        new_session = True

    csrf_token = (
        _make_csrf_token(session_id) if _env_bool("WEBAPP_CSRF_ENABLED", False) else ""
    )

    # SEO meta for home
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"{site_name} — Turn any page into clean Markdown"
    meta_description = "Render pages to clean Markdown, extract emails and links. Share public results with short keys and browse recent URLs."
    abs_page_url = _abs_url(request, "/")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # Optional banner when a crawl was just started (anonymous redirect target)
    submitted_id = request.query_params.get("submitted") or None
    submitted_status_url = f"/api/status/{submitted_id}" if submitted_id else None
    submitted_is_private = True if request.query_params.get("private") else False

    resp = templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "items": items,
            "csrf_token": csrf_token,
            "login_error": True if request.query_params.get("error") else False,
            # Submission banner
            "submitted_id": submitted_id,
            "submitted_status_url": submitted_status_url,
            "submitted_is_private": submitted_is_private,
            # SEO
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )
    if new_session:
        cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
        session_ttl = int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
        try:
            log_audit("session_created", request=request)
        except Exception:
            pass
        resp.set_cookie(
            key=cookie_name,
            value=session_id,
            max_age=session_ttl,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )
    return resp
