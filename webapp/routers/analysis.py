import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl, CrawlEmail, Product
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

        # Site branding
        site_name = os.getenv("SITE_NAME", "Markdownify Web App")

        # Build SEO-friendly page title
        try:
            if isinstance(payload, dict):
                scope_val = str(payload.get("scope") or "").strip().lower()
            else:
                scope_val = ""
        except Exception:
            scope_val = ""
        if not scope_val:
            scope_val = str(getattr(row, "scope", "") or "").strip().lower()
        domain_val = (row.domain or "").strip()
        path_val = (row.path or "").strip() or "/"
        if scope_val == "site":
            page_title = (
                f"{domain_val} Site Analysis — Pages, Links, Emails | {site_name}"
            )
        else:
            # Page scope: prefer page.title, else use path
            path_or_title = title_from_payload or path_val
            page_title = f"Page Analysis — {path_or_title} — {domain_val} | {site_name}"

        meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
            "markdown", ""
        )
        # Safe summary (simple heuristic to keep short)
        if meta_description and len(meta_description) > 300:
            meta_description = meta_description[:297] + "..."

        abs_page_url = _abs_url(request, f"/analysis/{row.id}")
        og_image_url = os.getenv("OG_IMAGE_URL") or None
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

        # Compute ownership/permissions for UI gating
        current_user = getattr(request.state, "current_user", None)
        is_owner = bool(current_user and getattr(row, "user_id", None) == current_user.id)
        status_lc = str(getattr(row, "status", "") or "").lower()
        logged_in = bool(current_user)
        # Chat: owners-only on succeeded analyses
        can_chat = (status_lc == "succeeded") and is_owner
        # Page selection and Shortcuts: owner-only on succeeded analyses
        can_select_pages = (status_lc == "succeeded") and is_owner

        # Query user products for compose section
        user_products = []
        if current_user:
            with get_session() as s:
                products = (
                    s.query(Product)
                    .filter(Product.user_id == current_user.id)
                    .order_by(Product.updated_at.desc())
                    .all()
                )
                user_products = [
                    {
                        "id": p.id,
                        "name": p.name or "",
                        "description": p.description or "",
                        "website": p.website or None,
                        "contact_info": p.contact_info or None,
                        "created_at": (
                            p.created_at or datetime.now(timezone.utc)
                        ).isoformat(),
                        "updated_at": (
                            p.updated_at or datetime.now(timezone.utc)
                        ).isoformat(),
                    }
                    for p in products
                ]

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
                # Ownership / gating
                "is_owner": is_owner,
                "can_chat": can_chat,
                "can_select_pages": can_select_pages,
                "user_products": user_products,
                "has_products": bool(user_products),
                "user_id": (current_user.id if current_user else ""),
                # SEO/Sharing
                "page_title": page_title,
                "meta_description": meta_description,
                "abs_page_url": abs_page_url,
                "og_image_url": og_image_url,
                "site_name": site_name,
                "json_ld": json_ld,
                # AI chat limits (mirror backend defaults; configurable via env)
                "ai_chat_max_pages": int(os.getenv("AI_CHAT_MAX_PAGES", "5")),
                "ai_chat_max_chars_per_page": int(
                    os.getenv("AI_CHAT_MAX_CHARS_PER_PAGE", "3000")
                ),
                "ai_chat_max_total_chars": int(
                    os.getenv("AI_CHAT_MAX_TOTAL_CHARS", "15000")
                ),
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

    # Site branding first
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")

    # Build SEO-friendly page title for public view
    try:
        if isinstance(payload, dict):
            scope_val = str(payload.get("scope") or "").strip().lower()
        else:
            scope_val = ""
    except Exception:
        scope_val = ""
    if not scope_val:
        scope_val = str(getattr(row, "scope", "") or "").strip().lower()
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    if scope_val == "site":
        page_title = f"{domain_val} Site Analysis — Pages, Links, Emails | {site_name}"
    else:
        # Page scope: prefer page.title, else use path
        path_or_title = title_from_payload or path_val
        page_title = f"Page Analysis — {path_or_title} — {domain_val} | {site_name}"

    meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
        "markdown", ""
    )
    if meta_description and len(meta_description) > 300:
        meta_description = meta_description[:297] + "..."

    abs_page_url = _abs_url(request, f"/analysis/{row.key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

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
            q = s.query(CrawlEmail.email).filter(CrawlEmail.crawl_id == row.id).distinct()
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

    # Claim eligibility inputs for public view (used by client-side countdown/UI)
    try:
        claim_min_hours = int(os.getenv("CLAIM_PUBLIC_MIN_AGE_HOURS", "24"))
    except Exception:
        claim_min_hours = 24
    created_at_iso = (row.created_at or datetime.now(timezone.utc)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    ownerless = getattr(row, "user_id", None) is None

    # Ownership/permissions (public view)
    current_user = getattr(request.state, "current_user", None)
    is_owner = bool(current_user and getattr(row, "user_id", None) == current_user.id)
    status_lc = str(getattr(row, "status", "") or "").lower()
    logged_in = bool(current_user)
    # Chat: owners-only on succeeded analyses
    can_chat = (status_lc == "succeeded") and is_owner
    # Page selection and Shortcuts: owner-only on succeeded analyses
    can_select_pages = (status_lc == "succeeded") and is_owner

    # Query user products for compose section
    user_products = []
    if current_user:
        with get_session() as s:
            products = (
                s.query(Product)
                .filter(Product.user_id == current_user.id)
                .order_by(Product.updated_at.desc())
                .all()
            )
            user_products = [
                {
                    "id": p.id,
                    "name": p.name or "",
                    "description": p.description or "",
                    "website": p.website or None,
                    "contact_info": p.contact_info or None,
                    "created_at": (
                        p.created_at or datetime.now(timezone.utc)
                    ).isoformat(),
                    "updated_at": (
                        p.updated_at or datetime.now(timezone.utc)
                    ).isoformat(),
                }
                for p in products
            ]

    template_name = "result_public.html" if not current_user else "result.html"
    resp = templates.TemplateResponse(
        template_name,
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
            # Ownership / gating
            "is_owner": is_owner,
            "can_chat": can_chat,
            "can_select_pages": can_select_pages,
            "user_products": user_products,
            "has_products": bool(user_products),
            "user_id": (current_user.id if current_user else ""),
            # Provide private id to owners for chat scoping
            "id": row.id,
            # SEO/Sharing
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": json_ld,
            # Claim eligibility (public ownerless)
            "created_at": created_at_iso,
            "claim_min_hours": claim_min_hours,
            "ownerless": ownerless,
            # Gating helpers for anonymous public
            "email_preview": email_preview,
            "email_count": email_count,
            # AI chat limits (mirror backend defaults; configurable via env)
            "ai_chat_max_pages": int(os.getenv("AI_CHAT_MAX_PAGES", "5")),
            "ai_chat_max_chars_per_page": int(
                os.getenv("AI_CHAT_MAX_CHARS_PER_PAGE", "3000")
            ),
            "ai_chat_max_total_chars": int(os.getenv("AI_CHAT_MAX_TOTAL_CHARS", "15000")),
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
