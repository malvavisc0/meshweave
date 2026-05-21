import json
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.reasons import friendly_reason
from webapp.utils.security import _make_csrf_token
from webapp.utils.summary import build_summary
from webapp.utils.url import _abs_url

router = APIRouter()

# Simple in-memory rate limiter for share toggle (max 5 per hour per user)
import time

share_toggle_limits = {}


@router.get("/analysis/shared/{share_key}", response_class=HTMLResponse)
async def view_shared_analysis(request: Request, share_key: str):
    """View private analysis via shareable link."""
    with get_session() as s:
        row = (
            s
            .query(Crawl)
            .filter(Crawl.share_key == share_key, Crawl.visibility == "private")
            .first()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    payload: dict | None = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            payload = None

    # Similar to public view, but with Unlisted badge and no claim
    # Compute SEO/meta
    title_from_payload = ""
    desc_from_payload = ""
    try:
        if payload:
            pg = payload.get("page") or {}
            title_from_payload = (pg.get("title") or "").strip()
            desc_from_payload = (pg.get("description") or "").strip()
    except Exception:
        pass

    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = str(getattr(row, "scope", "") or "").strip().lower()
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    if scope_val == "site":
        page_title = f"{domain_val} Site Analysis — Pages, Links, Emails | {site_name}"
    else:
        path_or_title = title_from_payload or path_val
        page_title = f"Page Analysis — {path_or_title} — {domain_val} | {site_name}"

    meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
        "markdown", ""
    )
    if meta_description and len(meta_description) > 300:
        meta_description = meta_description[:297] + "..."

    abs_page_url = _abs_url(request, f"/analysis/shared/{share_key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    summary = build_summary(row, payload)

    # Scrub emails for shared view (always scrub regardless of auth)
    payload_display = payload
    try:
        # Always scrub for shared view
        payload_copy = json.loads(json.dumps(payload or {}))

        def _scrub(obj):
            try:
                if isinstance(obj, dict):
                    for kk in list(obj.keys()):
                        lk = str(kk).lower()
                        if lk in ("emails", "emails_unique", "emails_by_url", "email"):
                            obj.pop(kk, None)
                            continue
                        _scrub(obj.get(kk))
                elif isinstance(obj, list):
                    for it in obj:
                        _scrub(it)
            except Exception:
                return

        _scrub(payload_copy)
        em = (payload or {}).get("emails") or {}
        payload_copy["emails"] = {
            "counts": em.get("counts") or {},
            "unique_count": len(em.get("unique") or []),
        }
        payload_display = payload_copy
    except Exception:
        try:
            payload_display = json.loads(json.dumps(payload or {}))
            payload_display.pop("emails", None)
        except Exception:
            payload_display = payload or {}

    template_name = "result_public.html"
    resp = templates.TemplateResponse(
        request,
        template_name,
        {
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "canonical_url": row.canonical_url,
            "visibility": "unlisted",  # Special badge
            "key": None,  # No key for shared
            "status": row.status,
            "error": row.error,
            "payload": payload_display,
            "api_url": f"/api/analysis/private/{row.id}",
            "abs_api_url": _abs_url(request, f"/api/analysis/private/{row.id}"),
            "summary": summary,
            "reason_stopped_label": (
                friendly_reason(payload.get("summary", {}).get("reason_stopped", ""))
                if payload and payload.get("summary")
                else ""
            ),
            "csrf_token": "",
            "is_owner": False,  # Shared viewers are not owners
            "user_id": "",
            "id": row.id,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": None,
            "created_at": "",
            "claim_min_hours": 24,
            "ownerless": False,
            "can_view_leads": False,
            "email_preview": [],
            "email_count": 0,
        },
    )
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


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

        payload: dict | None = None
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
        site_name = os.getenv("SITE_NAME", "MeshWeave")

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

        # JSON-LD: CreativeWork (LLM-first)
        try:
            # Derive counts from payload when available
            content_pages_count = 0
            emails_count = 0
            internal_links_count = 0
            external_links_count = 0
            if payload:
                try:
                    if payload.get("metrics") and payload["metrics"].get("extraction"):
                        ext = payload["metrics"]["extraction"]
                        # Do not use internal_count for content_pages_count (it's link count, not page count)
                        if ext.get("external_count") is not None:
                            external_links_count = int(ext.get("external_count") or 0)
                except Exception:
                    pass
                try:
                    if payload.get("links"):
                        if isinstance(payload["links"].get("internal"), list):
                            internal_links_count = len(payload["links"]["internal"])
                        if isinstance(payload["links"].get("external"), list):
                            external_links_count = max(
                                external_links_count, len(payload["links"]["external"])
                            )
                except Exception:
                    pass
                try:
                    if payload.get("emails") and payload["emails"].get("counts"):
                        emails_count = int(
                            payload["emails"]["counts"].get("total_unique") or 0
                        )
                except Exception:
                    pass
                try:
                    if isinstance(payload.get("pages"), list):
                        content_pages_count = len(payload["pages"])
                    elif (
                        isinstance(payload.get("summary"), dict)
                        and payload["summary"].get("visited_count") is not None
                    ):
                        content_pages_count = int(
                            payload["summary"]["visited_count"] or 0
                        )
                except Exception:
                    pass
            json_ld = json.dumps({
                "@context": "https://schema.org",
                "@type": "CreativeWork",
                "name": "MeshWeave Analysis",
                "identifier": str(row.id),
                "about": (row.domain or "").strip(),
                "url": abs_page_url,
                "dateModified": (row.updated_at or datetime.now(UTC)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "creativeWorkStatus": (row.status or "").title(),
                "measurementTechnique": [
                    "web-crawl",
                    "markdown-extraction",
                    "link-analysis",
                    "email-detection",
                ],
                "isAccessibleForFree": True,
                "keywords": [
                    "markdown",
                    "link map",
                    "email intelligence",
                    "ai summary",
                ],
                "additionalProperty": [
                    {
                        "@type": "PropertyValue",
                        "name": "content_pages_count",
                        "value": str(content_pages_count),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "emails_count",
                        "value": str(emails_count),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "internal_links_count",
                        "value": str(internal_links_count),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "external_links_count",
                        "value": str(external_links_count),
                    },
                ],
            })
        except Exception:
            json_ld = json.dumps({
                "@context": "https://schema.org",
                "@type": "CreativeWork",
                "name": "MeshWeave Analysis",
                "identifier": str(row.id),
                "about": (row.domain or "").strip(),
                "url": abs_page_url,
            })

        summary = build_summary(row, payload)

        reason_stopped_label = (
            friendly_reason(payload.get("summary", {}).get("reason_stopped", ""))
            if payload and payload.get("summary")
            else ""
        )

        api_url = f"/api/analysis/private/{row.id}"
        abs_api_url = _abs_url(request, api_url)

        # Compute ownership/permissions for UI gating
        current_user = getattr(request.state, "current_user", None)
        is_owner = bool(
            current_user and getattr(row, "user_id", None) == current_user.id
        )
        status_lc = str(getattr(row, "status", "") or "").lower()
        # Cooldown for retry
        refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
        now = datetime.now(UTC)
        next_retry_eligible = row.updated_at + timedelta(
            minutes=refresh_min_age_minutes
        )
        can_retry_cooldown = now >= next_retry_eligible
        can_retry = (row.status != "running") and can_retry_cooldown
        retry_eta = (
            f"{int((next_retry_eligible - now).total_seconds() / 60)}m"
            if not can_retry_cooldown
            else ""
        )

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
            request,
            "result.html",
            {
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
                "reason_stopped_label": reason_stopped_label,
                "api_url": api_url,
                "abs_api_url": abs_api_url,
                "can_retry": can_retry,
                "retry_eta": retry_eta,
                "csrf_token": csrf_token,
                # Ownership / gating
                "is_owner": is_owner,
                "user_id": (current_user.id if current_user else ""),
                "can_view_leads": is_owner,
                # SEO/Sharing
                "page_title": page_title,
                "meta_description": meta_description,
                "abs_page_url": abs_page_url,
                "og_image_url": og_image_url,
                "site_name": site_name,
                "json_ld": json_ld,
                # Owner toggles
                "listed": row.listed,
                "share_url": (
                    f"/analysis/shared/{row.share_key}" if row.share_key else ""
                ),
                "can_refresh": can_retry,
                "refresh_eta": retry_eta,
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
            s
            .query(Crawl)
            .filter(Crawl.key == ref, Crawl.visibility == "public")
            .one_or_none()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    payload: dict | None = None
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
    site_name = os.getenv("SITE_NAME", "MeshWeave")

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

    # JSON-LD: CreativeWork (LLM-first)
    try:
        # Derive counts from payload when available
        content_pages_count = 0
        emails_count = 0
        internal_links_count = 0
        external_links_count = 0
        if payload:
            try:
                if payload.get("metrics") and payload["metrics"].get("extraction"):
                    ext = payload["metrics"]["extraction"]
                    # Do not use internal_count for content_pages_count (it's link count, not page count)
                    if ext.get("external_count") is not None:
                        external_links_count = int(ext.get("external_count") or 0)
            except Exception:
                pass
            try:
                if payload.get("links"):
                    if isinstance(payload["links"].get("internal"), list):
                        internal_links_count = len(payload["links"]["internal"])
                    if isinstance(payload["links"].get("external"), list):
                        external_links_count = max(
                            external_links_count, len(payload["links"]["external"])
                        )
            except Exception:
                pass
            try:
                if payload.get("emails") and payload["emails"].get("counts"):
                    emails_count = int(
                        payload["emails"]["counts"].get("total_unique") or 0
                    )
            except Exception:
                pass
            try:
                if isinstance(payload.get("pages"), list):
                    content_pages_count = len(payload["pages"])
                elif (
                    isinstance(payload.get("summary"), dict)
                    and payload["summary"].get("visited_count") is not None
                ):
                    content_pages_count = int(payload["summary"]["visited_count"] or 0)
            except Exception:
                pass
        json_ld = json.dumps({
            "@context": "https://schema.org",
            "@type": "CreativeWork",
            "name": "MeshWeave Analysis",
            "identifier": str(row.key),
            "about": (row.domain or "").strip(),
            "url": abs_page_url,
            "dateModified": (row.updated_at or datetime.now(UTC)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "creativeWorkStatus": (row.status or "").title(),
            "measurementTechnique": [
                "web-crawl",
                "markdown-extraction",
                "link-analysis",
                "email-detection",
            ],
            "isAccessibleForFree": True,
            "keywords": [
                "markdown",
                "link map",
                "email intelligence",
                "ai summary",
            ],
            "additionalProperty": [
                {
                    "@type": "PropertyValue",
                    "name": "content_pages_count",
                    "value": str(content_pages_count),
                },
                {
                    "@type": "PropertyValue",
                    "name": "emails_count",
                    "value": str(emails_count),
                },
                {
                    "@type": "PropertyValue",
                    "name": "internal_links_count",
                    "value": str(internal_links_count),
                },
                {
                    "@type": "PropertyValue",
                    "name": "external_links_count",
                    "value": str(external_links_count),
                },
            ],
        })
    except Exception:
        json_ld = json.dumps({
            "@context": "https://schema.org",
            "@type": "CreativeWork",
            "name": "MeshWeave Analysis",
            "identifier": str(row.key),
            "about": (row.domain or "").strip(),
            "url": abs_page_url,
        })

    summary = build_summary(row, payload)

    reason_stopped_label = (
        friendly_reason(payload.get("summary", {}).get("reason_stopped", ""))
        if payload and payload.get("summary")
        else ""
    )

    # Email preview/count for anonymous gating (public view)
    email_preview = []
    email_count = 0
    try:
        # Extract emails from payload_json (CrawlEmail table removed)
        import json as _json

        emails_data = _json.loads(row.payload_json or "{}")
        all_emails_list = (emails_data.get("emails") or {}).get("unique") or []
        email_count = len(all_emails_list)
        email_preview = all_emails_list[:3]
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
    created_at_iso = (row.created_at or datetime.now(UTC)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    ownerless = getattr(row, "user_id", None) is None

    # Cooldown for refresh (public domain root)
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    now = datetime.now(UTC)
    can_refresh = False
    refresh_eta = ""
    with get_session() as s:
        public_root = (
            s
            .query(Crawl)
            .filter(
                Crawl.domain == row.domain,
                Crawl.visibility == "public",
                Crawl.path == "/",
                Crawl.query == "",
            )
            .first()
        )
        if public_root:
            next_refresh_eligible = public_root.updated_at + timedelta(
                minutes=refresh_min_age_minutes
            )
            can_refresh = now >= next_refresh_eligible
            if not can_refresh:
                refresh_eta = (
                    f"{int((next_refresh_eligible - now).total_seconds() / 60)}m"
                )

    # Ownership/permissions (public view)
    current_user = getattr(request.state, "current_user", None)
    is_owner = bool(current_user and getattr(row, "user_id", None) == current_user.id)
    status_lc = str(getattr(row, "status", "") or "").lower()
    # Sanitize payload for anonymous viewers to avoid leaking emails (top-level and per-page)
    payload_display = payload
    try:
        if not current_user:
            # Work on a safe deep copy of the JSON payload (falls back to {} when missing/bad)
            payload_copy = json.loads(json.dumps(payload or {}))

            # Recursive scrubber for any key that may contain emails
            def _scrub(obj):
                try:
                    if isinstance(obj, dict):
                        for kk in list(obj.keys()):
                            lk = str(kk).lower()
                            if lk in (
                                "emails",
                                "emails_unique",
                                "emails_by_url",
                                "email",
                            ):
                                obj.pop(kk, None)
                                continue
                            _scrub(obj.get(kk))
                    elif isinstance(obj, list):
                        for it in obj:
                            _scrub(it)
                except Exception:
                    return

            _scrub(payload_copy)

            # Re-introduce non-sensitive aggregates at the top-level (counts + unique_count)
            em = (payload or {}).get("emails") or {}
            payload_copy["emails"] = {
                "counts": em.get("counts") or {},
                "unique_count": len(em.get("unique") or []),
            }

            payload_display = payload_copy
    except Exception:
        # In worst case, drop emails entirely but keep the rest of the payload intact
        try:
            payload_display = json.loads(json.dumps(payload or {}))
            payload_display.pop("emails", None)
        except Exception:
            payload_display = payload or {}

    template_name = "result_public.html" if not current_user else "result.html"
    resp = templates.TemplateResponse(
        request,
        template_name,
        {
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "canonical_url": row.canonical_url,
            "visibility": row.visibility,
            "listed": row.listed,
            "key": row.key,
            "status": row.status,
            "error": row.error,
            "payload": payload_display,
            "api_url": api_url,
            "abs_api_url": abs_api_url,
            # Enriched
            "summary": summary,
            "reason_stopped_label": reason_stopped_label,
            "api_summary_url": api_summary_url,
            "emails_csv_url": emails_csv_url,
            "links_csv_url": links_csv_url,
            "top_domains_csv_url": top_domains_csv_url,
            "csrf_token": csrf_token,
            # Ownership / gating
            "is_owner": is_owner,
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
            "can_view_leads": True if current_user else False,
            # Refresh cooldown
            "can_refresh": can_refresh,
            "refresh_eta": refresh_eta,
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


@router.post("/analysis/{crawl_id}/set-listed")
async def set_listed(request: Request, crawl_id: str):
    """Set listed status for a public analysis (owner only)."""
    await require_auth(request)
    row = await require_ownership(request, crawl_id)
    if row.visibility != "public":
        raise HTTPException(
            status_code=400, detail="Only public analyses can be listed/unlisted"
        )

    try:
        data = await request.json()
        listed = bool(data.get("listed", True))
    except Exception:
        listed = True

    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if db_row:
            db_row.listed = listed
            s.commit()

    return {"ok": True, "listed": listed}


@router.post("/analysis/{crawl_id}/set-share")
async def set_share(request: Request, crawl_id: str):
    """Enable/disable shareable link for a private analysis (owner only)."""
    user = await require_auth(request)
    row = await require_ownership(request, crawl_id)
    if row.visibility != "private":
        raise HTTPException(
            status_code=400, detail="Only private analyses can have share links"
        )

    # Rate limiting: max 5 toggles per hour per user
    now = time.time()
    user_key = f"share_toggle_{user.id}"
    if user_key not in share_toggle_limits:
        share_toggle_limits[user_key] = []
    share_toggle_limits[user_key] = [
        t for t in share_toggle_limits[user_key] if now - t < 3600
    ]
    if len(share_toggle_limits[user_key]) >= 5:
        raise HTTPException(
            status_code=429, detail="Too many share toggles. Try again later."
        )
    share_toggle_limits[user_key].append(now)

    try:
        data = await request.json()
        enabled = bool(data.get("enabled", False))
    except Exception:
        enabled = False

    share_key = None
    if enabled:
        share_key = secrets.token_urlsafe(16)  # 32 chars
        # Ensure unique
        with get_session() as s:
            while s.query(Crawl).filter(Crawl.share_key == share_key).first():
                share_key = secrets.token_urlsafe(16)

    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if db_row:
            db_row.share_key = share_key
            s.commit()

    return {"ok": True, "enabled": enabled, "share_key": share_key}
