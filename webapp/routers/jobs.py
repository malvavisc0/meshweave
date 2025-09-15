import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl
from webapp.services.crawling import run_crawl_task
from webapp.services.site_crawling import run_site_crawl_task
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.quotas import (
    enforce_concurrent_jobs_limit,
    enforce_daily_site_crawl_limit,
)
from webapp.utils.security import _make_csrf_token, _verify_csrf_token
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/my", response_class=HTMLResponse)
async def my_jobs(
    request: Request,
    page_size: int = 25,
    cursor: Optional[str] = None,
    dir: Optional[str] = "next",
):
    """List current user's jobs with pagination (newest first)."""
    user = await require_auth(request)

    try:
        page_size = int(page_size)
    except Exception:
        page_size = 25
    if page_size not in (25, 50, 100):
        page_size = 25

    direction = (dir or "next").lower()
    if direction not in ("next", "prev"):
        direction = "next"

    with get_session() as s:
        rows_db: List[Crawl] = (
            s.query(Crawl)
            .filter(Crawl.user_id == user.id)
            .order_by(Crawl.updated_at.desc(), Crawl.id.desc())
            .limit(500)
            .all()
        )
    # Basic-first page subset (keyset scaffolding retained)
    rows = rows_db[:page_size]

    items = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "scope": r.scope or "page",
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "visibility": r.visibility,
                "status": r.status,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
            }
        )

    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"My Jobs — {site_name}"
    meta_description = "Your recent crawls."

    # CSRF token for retry forms on this page
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
        "my.html",
        {
            "request": request,
            "items": items,
            "has_prev": False,
            "has_next": False,
            "prev_url": None,
            "next_url": None,
            # Notices from query params for user feedback (e.g., after cancel)
            "notice": request.query_params.get("notice") or None,
            "notice_job": request.query_params.get("job") or None,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": _abs_url(request, "/my"),
            "csrf_token": csrf_token,
        },
    )

    # Set session cookie if newly created for CSRF
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


@router.get("/cancel")
async def cancel_crawl_no_id(request: Request):
    return RedirectResponse(url="/my?notice=cancel_get", status_code=303)


@router.get("/cancel/{crawl_id}")
async def cancel_crawl_get(request: Request, crawl_id: str):
    # Do not perform cancellation on GET to avoid CSRF; just inform user and redirect
    return RedirectResponse(url=f"/my?notice=cancel_get&job={crawl_id}", status_code=303)


@router.post("/cancel/{crawl_id}")
async def cancel_crawl(
    request: Request,
    crawl_id: str,
    csrf_token: Optional[str] = Form(None),
):
    """Cancel a running crawl (owner only)."""
    # CSRF validation
    if _env_bool("WEBAPP_CSRF_ENABLED", False):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
            return RedirectResponse(
                url=f"/my?notice=csrf_failed&job={crawl_id}", status_code=303
            )

    # Auth + owner
    await require_auth(request)
    try:
        row = await require_ownership(request, crawl_id)
    except HTTPException:
        return RedirectResponse(
            url=f"/my?notice=not_authorized&job={crawl_id}", status_code=303
        )

    if (row.status or "").lower() != "running":
        return RedirectResponse(
            url=f"/my?notice=not_running&job={crawl_id}", status_code=303
        )

    now = datetime.now(timezone.utc)
    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if not db_row:
            return RedirectResponse(
                url=f"/my?notice=not_found&job={crawl_id}", status_code=303
            )
        # Only transition running -> cancelled
        if (db_row.status or "").lower() != "running":
            return RedirectResponse(
                url=f"/my?notice=not_running&job={crawl_id}", status_code=303
            )
        db_row.status = "cancelled"
        db_row.error = "cancelled_by_user"
        db_row.updated_at = now

    return RedirectResponse(url=f"/my?notice=cancelled&job={crawl_id}", status_code=303)


@router.post("/retry/{crawl_id}")
async def retry_crawl(
    request: Request,
    background_tasks: BackgroundTasks,
    crawl_id: str,
    csrf_token: Optional[str] = Form(None),
):
    """Retry a crawl (owner only) when not running."""
    # CSRF validation
    if _env_bool("WEBAPP_CSRF_ENABLED", False):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    # Auth + owner
    user = await require_auth(request)
    row = await require_ownership(request, crawl_id)

    if row.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    # Enforce quotas
    enforce_concurrent_jobs_limit(user.id)
    if (row.scope or "page") == "site":
        enforce_daily_site_crawl_limit(user.id)

    # Reset and schedule
    now = datetime.now(timezone.utc)
    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if not db_row:
            raise HTTPException(status_code=404, detail="Not found")
        db_row.status = "pending"
        db_row.payload_json = None
        db_row.error = None
        try:
            if hasattr(db_row, "error_json"):
                setattr(db_row, "error_json", None)
        except Exception:
            pass
        db_row.updated_at = now

    # Schedule task with force_refresh=True
    if (row.scope or "page") == "site":
        background_tasks.add_task(run_site_crawl_task, crawl_id, True)
    else:
        background_tasks.add_task(run_crawl_task, crawl_id, True, user_id=user.id)

    return RedirectResponse(url=f"/my?notice=retried&job={crawl_id}", status_code=303)
