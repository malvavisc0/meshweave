import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl, CrawlEmail
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.security import _make_csrf_token
from webapp.utils.summary import build_summary
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/analysis/{ref}", response_class=HTMLResponse)
async def view_analysis(request: Request, ref: str):
    """Unified analysis view.

    If 'ref' is a UUID → private analysis (owner-only, claimable if anonymous).
    Else treat 'ref' as public short key.
    """
    # Try UUID → private
    is_uuid = False
    try:
        _ = uuid.UUID(ref)
        is_uuid = True
    except Exception:
        is_uuid = False

    if is_uuid:
        # Private (owner-only)
        # If this private job was created anonymously (no owner), allow the first authenticated
        # user reaching this page to claim ownership. Otherwise, enforce ownership.
        with get_session() as s:
            db_row = s.get(Crawl, ref)
            if not db_row:
                raise HTTPException(status_code=404, detail="Not found")
            if not getattr(db_row, "user_id", None):
                user = await require_auth(request)
                db_row.user_id = user.id
                s.flush()
                row = db_row
            else:
                row = await require_ownership(request, ref)

        payload: Optional[dict] = None
        if row.payload_json:
            try:
                payload = json.loads(row.payload_json)
            except json.JSONDecodeError:
                payload = None

        # Compute SEO/meta and summary for private view
        title_from_payload = ""
        desc_from_payload = ""
        try:
            if payload:
                pg = payload.get("page") or {}
                title_from_payload = (pg.get("title") or "").strip()
                desc_from_payload = (pg.get("description") or "").strip()
        except Exception:
            pass

        page_title = title_from_payload or f"Result for {row.canonical_url}"
        meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
            "markdown", ""
        )
        # Safe summary (simple heuristic to keep short)
        if meta_description and len(meta_description) > 300:
            meta_description = meta_description[:297] + "..."

        abs_page_url = _abs_url(request, f"/analysis/{row.id}")
        og_image_url = os.getenv("OG_IMAGE_URL") or None
        site_name = os.getenv("SITE_NAME", "Markdownify Web App")
        json_ld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": page_title,
                "description": meta_description,
                "url": abs_page_url,
                "dateModified": (row.updated_at or datetime.now(timezone.utc)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
        )

        summary = build_summary(row, payload)

        api_url = f"/api/analysis/private/{row.id}"
        abs_api_url = _abs_url(request, api_url)

        # CSRF token for retry form (generate new session if missing and CSRF is enabled)
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
            "result.html",
            {
                "request": request,
                "id": row.id,
                "domain": row.domain,
                "path": row.path,
                "query": row.query,
                "canonical_url": row.canonical_url,
                "visibility": row.visibility,
                "status": row.status,
                "error": row.error,
                "payload": payload,
                "summary": summary,
                "api_url": api_url,
                "abs_api_url": abs_api_url,
                "can_retry": (row.status != "running"),
                "csrf_token": csrf_token,
                # SEO/Sharing
                "page_title": page_title,
                "meta_description": meta_description,
                "abs_page_url": abs_page_url,
                "og_image_url": og_image_url,
                "site_name": site_name,
                "json_ld": json_ld,
            },
        )
        # Prevent indexing of private results
        resp.headers["X-Robots-Tag"] = "noindex"

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

    # Public by short key
    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.key == ref, Crawl.visibility == "public")
            .one_or_none()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    payload: Optional[dict] = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            payload = None

    # SEO/meta computation
    title_from_payload = ""
    desc_from_payload = ""
    try:
        if payload:
            pg = payload.get("page") or {}
            title_from_payload = (pg.get("title") or "").strip()
            desc_from_payload = (pg.get("description") or "").strip()
    except Exception:
        pass

    page_title = title_from_payload or f"Result for {row.canonical_url}"
    meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
        "markdown", ""
    )
    if meta_description and len(meta_description) > 300:
        meta_description = meta_description[:297] + "..."

    abs_page_url = _abs_url(request, f"/analysis/{row.key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")

    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": page_title,
            "description": meta_description,
            "url": abs_page_url,
            "dateModified": (row.updated_at or datetime.now(timezone.utc)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
    )

    summary = build_summary(row, payload)

    # Email preview/count for anonymous gating (public view)
    email_preview = []
    email_count = 0
    try:
        with get_session() as s:
            q = (
                s.query(CrawlEmail.email)
                .filter(CrawlEmail.crawl_id == row.id)
                .distinct()
            )
            email_count = q.count()
            preview_rows = q.limit(3).all()
            email_preview = [r[0] for r in preview_rows]
    except Exception:
        email_preview = []
        email_count = 0

    # CSV/summary endpoints
    api_summary_url = f"/api/analysis/public/{row.key}/summary"
    emails_csv_url = f"/api/analysis/public/{row.key}/emails.csv"
    links_csv_url = f"/api/analysis/public/{row.key}/links.csv"
    top_domains_csv_url = f"/api/analysis/public/{row.key}/top-external-domains.csv"
    api_url = f"/api/analysis/public/{row.key}"
    abs_api_url = _abs_url(request, api_url)

    # CSRF token for refresh form (generate new session if missing and CSRF is enabled)
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
        "result.html",
        {
            "request": request,
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "canonical_url": row.canonical_url,
            "visibility": row.visibility,
            "key": row.key,
            "status": row.status,
            "error": row.error,
            "payload": payload,
            "api_url": api_url,
            "abs_api_url": abs_api_url,
            # Enriched
            "summary": summary,
            "api_summary_url": api_summary_url,
            "emails_csv_url": emails_csv_url,
            "links_csv_url": links_csv_url,
            "top_domains_csv_url": top_domains_csv_url,
            "csrf_token": csrf_token,
            # SEO/Sharing
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": json_ld,
            # Gating helpers for anonymous public
            "email_preview": email_preview,
            "email_count": email_count,
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

    # Prevent indexing of non-succeeded public pages (avoid thin/placeholder content)
    if row.status != "succeeded":
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp
