import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, or_

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl, CrawlLink, Submission
from webapp.services.crawling import run_crawl_task
from webapp.services.site_crawling import run_site_crawl_task
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.http import _client_ip_from_request, _collect_headers_subset
from webapp.utils.logging import log_audit
from webapp.utils.quotas import (
    enforce_concurrent_jobs_limit,
    enforce_daily_site_crawl_limit,
)
from webapp.utils.security import _hash_ip, _make_csrf_token, _verify_csrf_token
from webapp.utils.url import (
    _abs_url,
    _safe_summary,
    canonicalize_url,
    generate_short_key,
    normalize_domain,
)

router = APIRouter()


def _build_summary(row: Crawl, payload: Optional[dict]) -> dict:
    """Build a computed summary object for a crawl payload.

    Parameters:
        row (Crawl): Database row for the analysis. Used as a fallback for values such as
            canonical URL and base domain if they are missing from the payload.
        payload (Optional[dict]): Parsed JSON payload produced by markdownify-crawler.
            May be None or partially malformed; this function is defensive against that.

    Returns:
        dict: Summary dictionary with the following shape:
            {
              "metrics": {
                "render": {
                  "final_url": str,
                  "response_status": int|None,
                  "network_requests": int|None,
                  "content_length": int|None,
                  "load_time_ms": float|int|None,
                  "cache_hit": bool|None,
                },
                "extraction": {
                  "base_domain": str,
                  "internal_count": int|None,
                  "external_count": int|None,
                  "total_candidates": int|None,
                  "unique_total": int|None,
                  "parse_time_ms": float|int|None,
                },
              },
              "emails": {
                "unique_count": int,
                "counts": dict,
              },
              "links": {
                "internal_count": int,
                "external_count": int,
                "top_external_domains": [{"domain": str, "count": int}, ...],
              },
              "seo_deltas": {
                "title_mismatch": bool,
                "description_mismatch": bool,
                "canonical_mismatch": bool,
                "og_missing": [str, ...],
              },
            }
        If any error occurs, an empty dict {} is returned.
    """
    summary: dict = {}
    try:
        payload_dict = payload if isinstance(payload, dict) else {}
        pg = payload_dict.get("page") or {}
        og = pg.get("og") or {}
        metrics = payload_dict.get("metrics") or {}
        render = metrics.get("render") or {}
        extraction = metrics.get("extraction") or {}
        lks = payload_dict.get("links") or {}
        em = payload_dict.get("emails") or {}

        base_domain = (extraction.get("base_domain") or row.domain or "").strip()

        # Top external domains
        top_ext: dict = {}
        for u in lks.get("external") or []:
            dom = normalize_domain(u)
            if dom:
                top_ext[dom] = top_ext.get(dom, 0) + 1
        top_external_domains = [
            {"domain": d, "count": c}
            for d, c in sorted(top_ext.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        def _t(s):
            """Convert a value to a stripped string.

            Args:
                s: Any value convertible to string.

            Returns:
                str: Stripped string or empty string on error.
            """
            try:
                return (s or "").strip()
            except Exception:
                return ""

        seo_deltas = {
            "title_mismatch": _t(pg.get("title")) != _t(og.get("title")),
            "description_mismatch": _t(pg.get("description"))
            != _t(og.get("description")),
            "canonical_mismatch": _t(pg.get("canonical")) != _t(row.canonical_url),
            "og_missing": [
                k for k in ("title", "description", "image", "url") if not _t(og.get(k))
            ],
        }

        summary = {
            "metrics": {
                "render": {
                    "final_url": render.get("final_url") or "",
                    "response_status": render.get("response_status"),
                    "network_requests": render.get("network_requests"),
                    "content_length": render.get("content_length"),
                    "load_time_ms": render.get("load_time_ms"),
                    "cache_hit": render.get("cache_hit"),
                },
                "extraction": {
                    "base_domain": base_domain,
                    "internal_count": extraction.get("internal_count"),
                    "external_count": extraction.get("external_count"),
                    "total_candidates": extraction.get("total_candidates"),
                    "unique_total": extraction.get("unique_total"),
                    "parse_time_ms": extraction.get("parse_time_ms"),
                },
            },
            "emails": {
                "unique_count": len(em.get("unique") or []),
                "counts": (em.get("counts") or {}),
            },
            "links": {
                "internal_count": len(lks.get("internal") or []),
                "external_count": len(lks.get("external") or []),
                "top_external_domains": top_external_domains,
            },
            "seo_deltas": seo_deltas,
        }
    except Exception:
        summary = {}
    return summary


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Homepage with submission form and latest 10 public results.

    Renders recent public crawls (domain, path, query, title, status) and ensures
    a session cookie and CSRF token are set.

    Args:
        request (Request): Incoming HTTP request.

    Returns:
        HTMLResponse: Rendered home page.
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
    meta_description = "Render pages to Markdown, extract emails and links. Share public results with short keys and browse recent URLs."
    abs_page_url = _abs_url(request, "/")
    og_image_url = os.getenv("OG_IMAGE_URL") or None
    # Optional banner when a crawl was just started (anonymous redirect target)
    submitted_id = request.query_params.get("submitted") or None
    submitted_status_url = f"/api/status/{submitted_id}" if submitted_id else None

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


@router.post("/submit")
async def submit(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    public: Optional[str] = Form(None),  # checkbox presence => public
    csrf_token: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
):
    """Handle form submission: upsert a crawl row and schedule a background task.

    Validates URL, optional origin/referrer, honeypot, CSRF, and simple rate limiting.
    Public submissions are upserted by (visibility, domain, path, query); private
    submissions create a new row.

    Args:
        request (Request): Incoming HTTP request (headers, cookies used for security and logging).
        background_tasks (BackgroundTasks): FastAPI background task runner.
        url (str): Absolute URL to crawl (http(s)://).
        public (Optional[str]): Presence indicates public visibility; None means private.
        csrf_token (Optional[str]): CSRF token to validate (when enabled).
        website (Optional[str]): Honeypot field; any non-empty value is rejected.

    Returns:
        RedirectResponse: 303 redirect to /analysis/public/{key} for public or /analysis/private/{id} for private.

    Raises:
        HTTPException: 400 invalid URL or honeypot; 403 origin/CSRF violations; 429 rate limiting; 500 key generation failure.
    """
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=400, detail="Invalid URL. Must start with http(s)://"
        )

    is_public = bool(public)
    user = getattr(request.state, "current_user", None)
    dom, path, query, canon_url = canonicalize_url(url)
    if not dom:
        raise HTTPException(status_code=400, detail="Unable to extract domain from URL")

    now = datetime.now(timezone.utc)

    # Security: origin validation, honeypot, CSRF, and simple rate limiting
    enforce_origin = _env_bool("WEBAPP_ENFORCE_ORIGIN", True)
    if enforce_origin:
        host_hdr = (request.headers.get("host") or "").lower()
        origin_hdr = request.headers.get("origin")
        referer_hdr = request.headers.get("referer")

        def _host_of(u: Optional[str]) -> str:
            """Extract lowercase host from a URL-like string.

            Args:
                u (Optional[str]): URL or origin string.

            Returns:
                str: Lowercased host without scheme/path, or empty string on error.
            """
            try:
                return (urlparse(u or "").netloc or "").lower()
            except Exception:
                return ""

        if origin_hdr:
            if _host_of(origin_hdr) != host_hdr:
                try:
                    log_audit(
                        "origin_not_allowed",
                        request=request,
                        level=logging.WARNING,
                        origin=origin_hdr,
                        host=host_hdr,
                    )
                except Exception:
                    pass
                raise HTTPException(status_code=403, detail="Origin not allowed")
        elif referer_hdr:
            if _host_of(referer_hdr) != host_hdr:
                try:
                    log_audit(
                        "referer_not_allowed",
                        request=request,
                        level=logging.WARNING,
                        referer=referer_hdr,
                        host=host_hdr,
                    )
                except Exception:
                    pass
                raise HTTPException(status_code=403, detail="Referer not allowed")

    # Honeypot field to deter bots
    if (website or "").strip():
        try:
            log_audit("honeypot_triggered", request=request, level=logging.WARNING)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Invalid submission")

    # CSRF validation
    if _env_bool("WEBAPP_CSRF_ENABLED", False):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
            try:
                log_audit(
                    "csrf_validation_failed", request=request, level=logging.WARNING
                )
            except Exception:
                pass
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    # Rate limit per client/session
    window_sec = int(os.getenv("WEBAPP_RATE_LIMIT_WINDOW_SEC", "60"))
    max_in_window = int(os.getenv("WEBAPP_RATE_LIMIT_MAX", "10"))
    trust_proxy = _env_bool("WEBAPP_TRUST_PROXY", False)
    ip_salt = os.getenv("IP_HASH_SALT", "")
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    client_ip_val = _client_ip_from_request(request, trust_proxy=trust_proxy)
    client_ip_hashed = _hash_ip(client_ip_val, ip_salt) if client_ip_val else None
    session_cookie_val = request.cookies.get(cookie_name)

    window_start = now - timedelta(seconds=window_sec)
    try:
        with get_session() as s:
            q = s.query(Submission).filter(Submission.created_at >= window_start)
            conds = []
            if client_ip_val:
                conds.append(Submission.client_ip == client_ip_val)
            if client_ip_hashed:
                conds.append(Submission.client_ip_hash == client_ip_hashed)
            if session_cookie_val:
                conds.append(Submission.session_id == session_cookie_val)
            if conds:
                q = q.filter(or_(*conds))
            recent_count = q.count()
            if recent_count >= max_in_window:
                try:
                    log_audit("rate_limited", request=request, level=logging.WARNING)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=429,
                    detail="Too many submissions. Please try again later.",
                )
    except HTTPException:
        raise
    except Exception:
        # On error, do not block but proceed (fail-open)
        pass

    visibility = "public" if is_public else "private"
    force_refresh = False

    # Upsert behavior:
    # - public: upsert by (visibility, domain, path, query)
    # - private: always create a new row (no upsert)
    with get_session() as s:
        crawl_id = None
        key_val: Optional[str] = None

        if is_public:
            existing = (
                s.query(Crawl)
                .filter(
                    Crawl.visibility == "public",
                    Crawl.domain == dom,
                    Crawl.path == path,
                    Crawl.query == query,
                )
                .one_or_none()
            )
            if existing:
                existing.url = url
                existing.canonical_url = canon_url
                # If a run is already in progress, keep it running; else reset to pending
                if existing.status not in {"running"}:
                    existing.status = "pending"
                    existing.payload_json = None
                    existing.error = None
                existing.updated_at = now
                crawl_id = existing.id
                key_val = existing.key
                force_refresh = True
            else:
                # Generate unique short key (retry on extremely rare collision)
                key_try = generate_short_key()
                dup = s.query(Crawl).filter(Crawl.key == key_try).one_or_none()
                tries = 0
                while dup is not None and tries < 3:
                    key_try = generate_short_key()
                    dup = s.query(Crawl).filter(Crawl.key == key_try).one_or_none()
                    tries += 1

                row = Crawl(
                    url=url,
                    domain=dom,
                    path=path,
                    query=query,
                    canonical_url=canon_url,
                    key=key_try,
                    visibility="public",
                    status="pending",
                    payload_json=None,
                    error=None,
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                s.flush()
                crawl_id = row.id
                key_val = row.key
        else:
            # Upsert behavior for private as well to satisfy unique constraint (visibility, domain, path, query)
            existing = (
                s.query(Crawl)
                .filter(
                    Crawl.visibility == "private",
                    Crawl.domain == dom,
                    Crawl.path == path,
                    Crawl.query == query,
                )
                .one_or_none()
            )
            if existing:
                # Update existing private row
                existing.url = url
                existing.canonical_url = canon_url
                # Attach ownership if authenticated and not already owned
                try:
                    if user and not getattr(existing, "user_id", None):
                        existing.user_id = getattr(user, "id", None)
                except Exception:
                    pass
                if existing.status not in {"running"}:
                    existing.status = "pending"
                    existing.payload_json = None
                    existing.error = None
                existing.updated_at = now
                crawl_id = existing.id
                # Ensure we refresh on resubmit
                force_refresh = True
            else:
                row = Crawl(
                    url=url,
                    domain=dom,
                    path=path,
                    query=query,
                    canonical_url=canon_url,
                    key=None,  # not exposed for private rows
                    visibility="private",
                    status="pending",
                    payload_json=None,
                    error=None,
                    user_id=(getattr(user, "id", None) if user else None),
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                s.flush()
                crawl_id = row.id

    # Schedule background crawl if not already running
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if row and row.status in {"pending", "failed", "succeeded"}:
            # start a new run when pending/failed/succeeded
            if user and getattr(user, "id", None):
                background_tasks.add_task(
                    run_crawl_task, crawl_id, force_refresh, user_id=user.id
                )
            else:
                background_tasks.add_task(run_crawl_task, crawl_id, force_refresh)

    # Capture submission metadata (configurable)
    if _env_bool("WEBAPP_LOG_REQUESTS", True):
        trust_proxy = _env_bool("WEBAPP_TRUST_PROXY", False)
        mask_ip = _env_bool("WEBAPP_MASK_IP", False)
        log_headers = _env_bool("WEBAPP_LOG_HEADERS", True)
        log_cookies = _env_bool("WEBAPP_LOG_COOKIES", False)
        ip_salt = os.getenv("IP_HASH_SALT", "")
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")

        # Client identity
        client_ip = _client_ip_from_request(request, trust_proxy=trust_proxy)
        client_ip_hash = _hash_ip(client_ip, ip_salt) if (client_ip and mask_ip) else None
        raw_client_ip = None if mask_ip else (client_ip or None)

        headers_subset = _collect_headers_subset(request) if log_headers else None

        # Cookies: optionally log, but exclude our own session id to avoid correlating via DB
        cookies_obj = {}
        try:
            if log_cookies and getattr(request, "cookies", None):
                for k, v in request.cookies.items():
                    if k == cookie_name:
                        continue
                    cookies_obj[k] = v
        except Exception:
            cookies_obj = {}

        # Determine status at submit time (after upsert)
        status_at_submit = "pending"
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if row and row.status:
                status_at_submit = row.status

        # Persist submission log
        with get_session() as s:
            s.add(
                Submission(
                    crawl_id=crawl_id,
                    domain=dom,
                    url_at_submit=url,
                    visibility=visibility,
                    force_refresh=force_refresh,
                    status_at_submit=status_at_submit,
                    client_ip=raw_client_ip if raw_client_ip else None,
                    client_ip_hash=client_ip_hash,
                    forwarded_for=(request.headers.get("x-forwarded-for") or None),
                    x_real_ip=(request.headers.get("x-real-ip") or None),
                    user_agent=(request.headers.get("user-agent") or None),
                    accept_language=(request.headers.get("accept-language") or None),
                    referer=(request.headers.get("referer") or None),
                    origin=(request.headers.get("origin") or None),
                    host=(request.headers.get("host") or None),
                    session_id=(
                        request.cookies.get(os.getenv("WEBAPP_COOKIE_NAME", "sid"))
                        or None
                    ),
                    headers_json=(json.dumps(headers_subset) if headers_subset else None),
                    cookies_json=(json.dumps(cookies_obj) if cookies_obj else None),
                )
            )

    # Build redirect response and set cookie if new
    if is_public:
        if not key_val:
            # Should not happen, but guard
            raise HTTPException(status_code=500, detail="Key generation failed")
        resp = RedirectResponse(url=f"/analysis/public/{key_val}", status_code=303)
    else:
        resp = RedirectResponse(url=f"/analysis/private/{crawl_id}", status_code=303)

    cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
    session_ttl = int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
    # Rotate session on every submit to reduce fixation risk
    new_session_id = str(uuid.uuid4())
    resp.set_cookie(
        key=cookie_name,
        value=new_session_id,
        max_age=session_ttl,
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
    )

    return resp


@router.get("/analysis/public/{key}", response_class=HTMLResponse)
async def view_public_by_key(request: Request, key: str):
    """Public result page by short key.

    Args:
        request (Request): Incoming HTTP request.
        key (str): Short public key identifying a Crawl.

    Returns:
        HTMLResponse: Rendered result page for the public crawl.

    Raises:
        HTTPException: 404 if not found.
    """
    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.key == key, Crawl.visibility == "public")
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
    meta_description = _safe_summary(desc_from_payload) or _safe_summary(
        (payload or {}).get("markdown", "")
    )

    abs_page_url = _abs_url(request, f"/analysis/public/{row.key}")
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

    summary = _build_summary(row, payload)

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
        },
    )
    # Set session cookie if newly created for CSRF
    if new_session and session_id:
        cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
        session_ttl = int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
        try:
            log_audit("session_created", request=request)
        except Exception:
            pass
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


@router.post("/submit-site")
async def submit_site(
    request: Request,
    background_tasks: BackgroundTasks,
    domain: str = Form(...),
    max_pages: Optional[str] = Form(None),
    max_depth: Optional[str] = Form(None),
    time_budget_ms: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
):
    """Start a site crawl (auth required).

    Validates CSRF, enforces user quotas, creates a private 'site' scope crawl owned by the user,
    and schedules a site crawl background task.

    Args:
        request (Request): Incoming request used for CSRF, auth, and metadata logging.
        background_tasks (BackgroundTasks): FastAPI background task runner.
        domain (str): Base domain to crawl (e.g., "example.com").
        max_pages (Optional[int]): Optional requested page limit.
        max_depth (Optional[int]): Optional requested crawl depth (0 means start page only).
        time_budget_ms (Optional[int]): Optional time budget in milliseconds.
        csrf_token (Optional[str]): CSRF token to validate when CSRF is enabled.

    Returns:
        RedirectResponse: 303 redirect to /analysis/private/{id} for the created job.

    Raises:
        HTTPException: 403 CSRF failure; 400 invalid domain; 429 quota violations.
    """
    # CSRF validation (same as other POSTs)
    if _env_bool("WEBAPP_CSRF_ENABLED", False):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
            return RedirectResponse(url="/my?notice=csrf_failed", status_code=303)

    user = getattr(request.state, "current_user", None)

    # Normalize and validate domain
    dom = (domain or "").strip().lower()
    if dom.startswith("http://") or dom.startswith("https://"):
        try:
            from urllib.parse import urlsplit

            dom = (urlsplit(dom).netloc or "").lower()
        except Exception:
            pass
    if dom.startswith("www."):
        dom = dom[4:]
    if not dom or "." not in dom or any(c.isspace() for c in dom):
        raise HTTPException(status_code=400, detail="Invalid domain")

    # Enforce quotas only when authenticated (no rate limits for anonymous)
    if user and getattr(user, "id", None):
        enforce_concurrent_jobs_limit(user.id)
        enforce_daily_site_crawl_limit(user.id)

    # Resolve limits and apply caps via env (we store raw requested, caps enforced in service)
    lim_req = {}
    if max_pages is not None:
        try:
            lim_req["max_pages"] = int(max_pages)
        except Exception:
            pass
    if max_depth is not None:
        try:
            lim_req["max_depth"] = int(max_depth)
        except Exception:
            pass
    if time_budget_ms is not None:
        try:
            lim_req["time_budget_ms"] = int(time_budget_ms)
        except Exception:
            pass

    # Upsert crawl row (private, unique on visibility+domain+path+query)
    start_url = f"https://{dom}/"
    now = datetime.now(timezone.utc)
    with get_session() as s:
        existing = (
            s.query(Crawl)
            .filter(
                Crawl.visibility == "private",
                Crawl.domain == dom,
                Crawl.path == "/",
                Crawl.query == "",
            )
            .one_or_none()
        )
        if existing:
            # Refresh existing private crawl (convert to site scope if needed)
            # Recover from stale 'running' (e.g., worker died) — flip back to pending if older than threshold
            try:
                stale_min = int(os.getenv("SITE_CRAWL_STALE_MINUTES", "10"))
            except Exception:
                stale_min = 10
            if (
                str(getattr(existing, "status", "")).lower() == "running"
                and getattr(existing, "updated_at", None)
                and (now - existing.updated_at) > timedelta(minutes=stale_min)
            ):
                existing.status = "pending"
                existing.payload_json = None
                existing.error = None

            existing.url = start_url
            existing.canonical_url = start_url
            existing.scope = "site"
            existing.limits_json = json.dumps(lim_req) if lim_req else json.dumps({})
            # Attach ownership when logged in and missing
            try:
                if user and not getattr(existing, "user_id", None):
                    existing.user_id = getattr(user, "id", None)
            except Exception:
                pass
            if existing.status != "running":
                existing.status = "pending"
                existing.payload_json = None
                existing.error = None
            existing.updated_at = now
            crawl_id = existing.id
        else:
            row = Crawl(
                url=start_url,
                domain=dom,
                path="/",
                query="",
                canonical_url=start_url,
                key=None,
                visibility="private",  # Option B: keep anonymous site crawls private
                scope="site",
                status="pending",
                payload_json=None,
                error=None,
                user_id=(getattr(user, "id", None) if user else None),
                limits_json=(json.dumps(lim_req) if lim_req else json.dumps({})),
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.flush()
            crawl_id = row.id

    # Schedule site crawl (robust): BackgroundTasks + immediate task (guarded by DB transition)
    background_tasks.add_task(run_site_crawl_task, crawl_id, False)
    try:
        asyncio.create_task(run_site_crawl_task(crawl_id, False))
    except Exception:
        pass
    try:
        log_audit("site_crawl_enqueued", request=request, crawl_id=crawl_id)
    except Exception:
        pass

    # Redirect:
    # - Logged-in owners go to private result
    # - Anonymous users go back to home with a status hint (private page is owner-protected)
    if user and getattr(user, "id", None):
        return RedirectResponse(url=f"/analysis/private/{crawl_id}", status_code=303)
    else:
        return RedirectResponse(url=f"/?submitted={crawl_id}", status_code=303)


@router.get("/api/progress/{crawl_id}")
async def api_progress(request: Request, crawl_id: str):
    """Return lightweight progress info for a private crawl (owner only).

    Args:
        request (Request): Incoming request (used for ownership check).
        crawl_id (str): UUID of the crawl.

    Returns:
        dict: {
          "id": str,
          "status": str,
          "scope": "page"|"site",
          "visited_pages": int,
          "limits": {...} | {},
          "elapsed_ms": int | None,
          "last_updated": ISO timestamp
        }
    """
    row = await require_ownership(request, crawl_id)
    now = datetime.now(timezone.utc)

    # Count distinct page_url's we have already persisted (works for both page/site)
    visited_pages = 0
    with get_session() as s:
        visited_pages = (
            s.query(CrawlLink.page_url)
            .filter(CrawlLink.crawl_id == crawl_id)
            .distinct()
            .count()
        )

    # Limits (for site crawls)
    limits = {}
    if (row.scope or "page") == "site":
        try:
            limits = json.loads(row.limits_json or "{}")
        except Exception:
            limits = {}
        # Fallback if effective limits not yet persisted
        if not isinstance(limits, dict):
            limits = {}
        if ("max_pages" not in limits) or (not limits.get("max_pages")):
            try:
                limits["max_pages"] = int(os.getenv("AUTH_SITE_MAX_PAGES_DEFAULT", "200"))
            except Exception:
                limits["max_pages"] = 200

    # Best-effort elapsed: time since updated_at while running (approximation)
    elapsed_ms = None
    try:
        if (row.status or "").lower() == "running" and row.updated_at:
            elapsed_ms = int((now - row.updated_at).total_seconds() * 1000)
    except Exception:
        elapsed_ms = None

    # Estimate remaining time for site crawls
    est_remaining_ms = None
    time_budget_ms_val = None
    time_budget_remaining_ms = None
    try:
        if (row.scope or "page") == "site":
            # ensure integer max_pages
            total = None
            v_total = limits.get("max_pages") if isinstance(limits, dict) else None
            try:
                total = int(v_total) if v_total is not None else None
            except Exception:
                total = None
            done = int(visited_pages or 0)
            if elapsed_ms is not None and total and total > 0 and done > 0:
                avg = float(elapsed_ms) / float(done)
                rem_pages = max(0, total - done)
                est_remaining_ms = int(avg * rem_pages)
            # time budget info if available
            v_budget = limits.get("time_budget_ms") if isinstance(limits, dict) else None
            try:
                time_budget_ms_val = int(v_budget) if v_budget is not None else None
            except Exception:
                time_budget_ms_val = None
            if time_budget_ms_val is not None and elapsed_ms is not None:
                time_budget_remaining_ms = max(
                    0, int(time_budget_ms_val) - int(elapsed_ms)
                )
    except Exception:
        est_remaining_ms = None
        time_budget_remaining_ms = None

    return {
        "id": row.id,
        "status": row.status,
        "scope": row.scope or "page",
        "visited_pages": visited_pages,
        "limits": limits,
        "elapsed_ms": elapsed_ms,
        "est_remaining_ms": est_remaining_ms,
        "time_budget_ms": time_budget_ms_val,
        "time_budget_remaining_ms": time_budget_remaining_ms,
        "last_updated": (row.updated_at or now).isoformat(),
    }


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
    """Cancel a running crawl (owner only).

    - Requires auth + ownership.
    - CSRF required when enabled.
    - Sets status='cancelled' and error='cancelled_by_user' if currently running.
    - Redirects back to the private result page.

    Args:
        request (Request): Incoming request for CSRF and ownership checks.
        crawl_id (str): UUID of the crawl to cancel.
        csrf_token (Optional[str]): CSRF token string when CSRF is enabled.

    Returns:
        RedirectResponse: 303 redirect to /analysis/private/{crawl_id}.

    Raises:
        HTTPException: 403 CSRF failure; 400 when job is not running; 404 if not found or not owned.
    """
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
    try:
        log_audit("crawl_cancel_requested", request=request, crawl_id=crawl_id)
    except Exception:
        pass

    return RedirectResponse(url=f"/my?notice=cancelled&job={crawl_id}", status_code=303)


@router.get("/my", response_class=HTMLResponse)
async def my_jobs(
    request: Request,
    page_size: int = 25,
    cursor: Optional[str] = None,
    dir: Optional[str] = "next",
):
    """List current user's jobs with pagination (newest first).

    Args:
        request (Request): Incoming request used to identify the user.
        page_size (int): Page size (25, 50, or 100). Defaults to 25.
        cursor (Optional[str]): Keyset cursor of form "epoch:id" for pagination.
        dir (Optional[str]): "next" or "prev" page direction. Defaults to "next".

    Returns:
        HTMLResponse: Rendered page with the user's recent jobs and pagination links.
    """
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

    def _cursor_of(row: Crawl) -> str:
        """Build a stable cursor string for keyset pagination.

        Args:
            row (Crawl): Crawl row whose updated_at and id are used.

        Returns:
            str: Cursor of the form "epoch:id".
        """
        ts = int((row.updated_at or datetime.now(timezone.utc)).timestamp())
        return f"{ts}:{row.id}"

    prev_url = None
    next_url = None

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
            "has_prev": True if prev_url else False,
            "has_next": True if next_url else False,
            "prev_url": prev_url,
            "next_url": next_url,
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
        try:
            log_audit("session_created", request=request)
        except Exception:
            pass
        resp.set_cookie(
            key=cookie_name,
            value=str(session_id),
            max_age=session_ttl,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )
    return resp


@router.post("/retry/{crawl_id}")
async def retry_crawl(
    request: Request,
    background_tasks: BackgroundTasks,
    crawl_id: str,
    csrf_token: Optional[str] = Form(None),
):
    """Retry a crawl (owner only) when not running.

    - Requires auth + ownership.
    - CSRF required.
    - Resets status to pending, clears payload/error, updates updated_at.
    - Schedules run with force_refresh=True.
    - Quotas:
        * Concurrent jobs (all user-owned jobs)
        * Daily site crawls count only when retrying a site crawl

    Args:
        request (Request): Incoming request used for CSRF and auth.
        background_tasks (BackgroundTasks): FastAPI background task runner.
        crawl_id (str): UUID of the crawl to retry.
        csrf_token (Optional[str]): CSRF token string when CSRF is enabled.

    Returns:
        RedirectResponse: 303 redirect to /analysis/private/{crawl_id}.

    Raises:
        HTTPException: 403 CSRF failure; 400 when job is already running; 404 if not found.
    """
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

    # Preconditions
    if row.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    # Enforce quotas
    # - Always enforce concurrent jobs
    enforce_concurrent_jobs_limit(user.id)
    # - Daily site crawls only when retrying a site crawl
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
        # Defensive: clear any future structured error field if present
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


@router.get("/domain/{domain}", response_class=HTMLResponse)
async def view_domain_index(
    request: Request,
    domain: str,
    page_size: int = 50,
    cursor: Optional[str] = None,
    dir: Optional[str] = "next",
):
    """List public results for a given domain.

    Args:
        request (Request): Incoming HTTP request.
        domain (str): Domain (host) to filter results by.

    Returns:
        HTMLResponse: Rendered domain index page.

    Raises:
        HTTPException: 404 if no public results exist for the domain.
    """
    # Normalize inputs
    dom = (domain or "").lower()
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 50
    if page_size not in (25, 50, 100):
        page_size = 50

    direction = (dir or "next").lower()
    if direction not in ("next", "prev"):
        direction = "next"

    # Parse cursor "epoch:id"
    cursor_ts = None
    cursor_id = None
    if cursor:
        try:
            parts = cursor.split(":", 1)
            cursor_ts = datetime.fromtimestamp(int(parts[0]), tz=timezone.utc)
            cursor_id = parts[1]
        except Exception:
            cursor_ts = None
            cursor_id = None

    # Query rows with keyset filters
    with get_session() as s:
        q = s.query(Crawl).filter(Crawl.domain == dom, Crawl.visibility == "public")
        if cursor_ts and cursor_id:
            if direction == "next":
                q = q.filter(
                    or_(
                        Crawl.updated_at < cursor_ts,
                        and_(Crawl.updated_at == cursor_ts, Crawl.id < cursor_id),
                    )
                ).order_by(Crawl.updated_at.desc(), Crawl.id.desc())
            else:
                q = q.filter(
                    or_(
                        Crawl.updated_at > cursor_ts,
                        and_(Crawl.updated_at == cursor_ts, Crawl.id > cursor_id),
                    )
                ).order_by(Crawl.updated_at.asc(), Crawl.id.asc())
        else:
            q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())
        rows_db: List[Crawl] = q.limit(page_size + 1).all()

    if direction == "prev" and cursor_ts and cursor_id:
        if len(rows_db) > page_size:
            rows_db = rows_db[-page_size:]
        rows_dom = list(reversed(rows_db))
    else:
        rows_dom = rows_db[:page_size]

    # Build items
    items = []
    for r in rows_dom:
        items.append(
            {
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "key": r.key,
                "status": r.status,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
            }
        )

    # Build cursors and URLs
    def _cursor_of(row: Crawl) -> str:
        """Build a stable cursor string for keyset pagination.

        Args:
            row (Crawl): Crawl row whose updated_at and id are used.

        Returns:
            str: Cursor of the form "epoch:id".
        """
        ts = int((row.updated_at or datetime.now(timezone.utc)).timestamp())
        return f"{ts}:{row.id}"

    prev_url = None
    next_url = None
    if rows_dom:
        first = rows_dom[0]
        last = rows_dom[-1]
        base_params = {"page_size": str(page_size)}
        prev_params = dict(base_params)
        prev_params["cursor"] = _cursor_of(first)
        prev_params["dir"] = "prev"
        prev_url = f"/domain/{dom}?" + urlencode(prev_params)

        next_params = dict(base_params)
        next_params["cursor"] = _cursor_of(last)
        next_params["dir"] = "next"
        next_url = f"/domain/{dom}?" + urlencode(next_params)

    # SEO meta for domain index
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"Public results for {dom} — {site_name}"
    meta_description = (
        f"Latest crawls for {dom}. View shareable Markdown, links, emails, and metrics."
    )
    abs_page_url = _abs_url(request, f"/domain/{dom}")

    # Absolute prev/next for link rel
    abs_prev_url = _abs_url(request, prev_url) if prev_url else None
    abs_next_url = _abs_url(request, next_url) if next_url else None
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    return templates.TemplateResponse(
        "domain_index.html",
        {
            "request": request,
            "domain": dom,
            "items": items,
            "page_size": page_size,
            "has_prev": True if prev_url else False,
            "has_next": True if next_url else False,
            "prev_url": prev_url,
            "next_url": next_url,
            "abs_prev_url": abs_prev_url,
            "abs_next_url": abs_next_url,
            # SEO
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )


@router.get("/all", response_class=HTMLResponse)
async def view_all(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    cursor: Optional[str] = None,
    dir: Optional[str] = "next",
):
    """Paginated listing of public results with optional filters.

    Query parameters:
      - domain: exact host (lowercase, 'www.' stripped)
      - status: one of {'pending','running','succeeded','failed'}

    Args:
        request (Request): Incoming HTTP request.
        page (int, optional): 1-based page index. Defaults to 1.
        page_size (int, optional): Page size between 10 and 100. Defaults to 50.
        domain (Optional[str], optional): Filter by domain. Defaults to None.
        status (Optional[str], optional): Filter by crawl status. Defaults to None.

    Returns:
        HTMLResponse: Rendered list page.
    """
    # Normalize inputs (keyset pagination)
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 50
    # Standardize to a small set for UX/caching
    if page_size not in (25, 50, 100):
        page_size = 50

    dom = None
    if domain:
        dom = domain.strip().lower()
        if dom.startswith("www."):
            dom = dom[4:]

    allowed_status = {"pending", "running", "succeeded", "failed"}
    st = status if (status and status in allowed_status) else None

    direction = (dir or "next").lower()
    if direction not in ("next", "prev"):
        direction = "next"

    # Parse cursor of form "epoch:id"
    cursor_ts = None
    cursor_id = None
    if cursor:
        try:
            parts = cursor.split(":", 1)
            cursor_ts = datetime.fromtimestamp(int(parts[0]), tz=timezone.utc)
            cursor_id = parts[1]
        except Exception:
            cursor_ts = None
            cursor_id = None

    # Query rows with keyset filters
    with get_session() as s:
        q = s.query(Crawl).filter(Crawl.visibility == "public")
        if dom:
            q = q.filter(Crawl.domain == dom)
        if st:
            q = q.filter(Crawl.status == st)

        if cursor_ts and cursor_id:
            if direction == "next":
                # Older than cursor (since we order DESC by default)
                q = q.filter(
                    or_(
                        Crawl.updated_at < cursor_ts,
                        and_(Crawl.updated_at == cursor_ts, Crawl.id < cursor_id),
                    )
                )
                q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())
            else:
                # Newer than cursor, fetch ASC then reverse for display
                q = q.filter(
                    or_(
                        Crawl.updated_at > cursor_ts,
                        and_(Crawl.updated_at == cursor_ts, Crawl.id > cursor_id),
                    )
                )
                q = q.order_by(Crawl.updated_at.asc(), Crawl.id.asc())
        else:
            q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())

        rows_db: List[Crawl] = q.limit(page_size + 1).all()

    # Build current page items and determine cursors
    has_prev = False
    has_next = False
    # No pagination: show latest up to 500 items
    rows = rows_db

    # Build items
    items = []
    for r in rows:
        title = ""
        try:
            if r.payload_json:
                payload = json.loads(r.payload_json)
                title = (payload.get("page") or {}).get("title") or ""
        except json.JSONDecodeError:
            title = ""
        items.append(
            {
                "key": r.key,
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "title": title,
                "status": r.status,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
            }
        )

    # Build prev/next URLs using first/last item cursors
    def _cursor_of(row: Crawl) -> str:
        """Build a stable cursor string for keyset pagination.

        Args:
            row (Crawl): Crawl row whose updated_at and id are used.

        Returns:
            str: Cursor of the form "epoch:id".
        """
        ts = int((row.updated_at or datetime.now(timezone.utc)).timestamp())
        return f"{ts}:{row.id}"

    prev_url = None
    next_url = None
    if items:
        first = rows[0]
        last = rows[-1]
        base_params = {}
        if dom:
            base_params["domain"] = dom
        if st:
            base_params["status"] = st
        base_params["page_size"] = str(page_size)

        # Prev points to newer items than 'first'
        prev_params = dict(base_params)
        prev_params["cursor"] = _cursor_of(first)
        prev_params["dir"] = "prev"
        prev_url = "/all?" + urlencode(prev_params)

        # Next points to older items than 'last'
        next_params = dict(base_params)
        next_params["cursor"] = _cursor_of(last)
        next_params["dir"] = "next"
        next_url = "/all?" + urlencode(next_params)

    # SEO
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    if dom and st:
        page_title = f"All public results for {dom} ({st}) — {site_name}"
        meta_description = (
            f"Browse public results for {dom} with status {st}. Filter and paginate."
        )
    elif dom:
        page_title = f"All public results for {dom} — {site_name}"
        meta_description = f"Browse public results for {dom}. Filter and paginate."
    elif st:
        page_title = f"All public results — status {st} — {site_name}"
        meta_description = (
            f"Browse public results filtered by status {st}. Filter and paginate."
        )
    else:
        page_title = f"All public results — {site_name}"
        meta_description = "Browse all public results. Filter by domain or status, and paginate through the list."

    # Canonical: keep domain/status only; exclude cursor/page_size
    canonical_params = {}
    if dom:
        canonical_params["domain"] = dom
    if st:
        canonical_params["status"] = st
    canonical_path = "/all"
    if canonical_params:
        canonical_path = canonical_path + "?" + urlencode(canonical_params)
    abs_page_url = _abs_url(request, canonical_path)

    # Absolute prev/next URLs for link rel hints
    abs_prev_url = _abs_url(request, prev_url) if prev_url else None
    abs_next_url = _abs_url(request, next_url) if next_url else None

    og_image_url = os.getenv("OG_IMAGE_URL") or None

    return templates.TemplateResponse(
        "all.html",
        {
            "request": request,
            "items": items,
            "page": page,
            "page_size": page_size,
            "has_prev": has_prev if prev_url else False,
            "has_next": has_next if next_url else False,
            "prev_url": prev_url,
            "next_url": next_url,
            "abs_prev_url": abs_prev_url,
            "abs_next_url": abs_next_url,
            "filter_domain": dom or "",
            "filter_status": st or "",
            # SEO
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )


@router.get("/analysis/private/{crawl_id}", response_class=HTMLResponse)
async def view_private(request: Request, crawl_id: str):
    """Private result page by crawl UUID.

    Args:
        request (Request): Incoming HTTP request.
        crawl_id (str): UUID of the Crawl row.

    Returns:
        HTMLResponse: Rendered result page for the private crawl.

    Raises:
        HTTPException: 404 if not found.
    """
    row = await require_ownership(request, crawl_id)

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
    meta_description = _safe_summary(desc_from_payload) or _safe_summary(
        (payload or {}).get("markdown", "")
    )
    abs_page_url = _abs_url(request, f"/analysis/private/{row.id}")
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

    summary = _build_summary(row, payload)
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
        try:
            log_audit("session_created", request=request)
        except Exception:
            pass
        resp.set_cookie(
            key=cookie_name,
            value=str(session_id),
            max_age=session_ttl,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )
    return resp
