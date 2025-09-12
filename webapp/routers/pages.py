import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl, Submission
from webapp.services.crawling import run_crawl_task
from webapp.utils.config import _env_bool
from webapp.utils.http import _client_ip_from_request, _collect_headers_subset
from webapp.utils.logging import log_audit
from webapp.utils.security import _hash_ip, _make_csrf_token, _verify_csrf_token
from webapp.utils.url import _abs_url, _safe_summary, canonicalize_url, generate_short_key

router = APIRouter()


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

    resp = templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "items": items,
            "csrf_token": csrf_token,
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
            "api_url": f"/api/analysis/public/{row.key}",
            # SEO/Sharing
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": json_ld,
        },
    )
    # Prevent indexing of non-succeeded public pages (avoid thin/placeholder content)
    if row.status != "succeeded":
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/domain/{domain}", response_class=HTMLResponse)
async def view_domain_index(request: Request, domain: str):
    """List public results for a given domain.

    Args:
        request (Request): Incoming HTTP request.
        domain (str): Domain (host) to filter results by.

    Returns:
        HTMLResponse: Rendered domain index page.

    Raises:
        HTTPException: 404 if no public results exist for the domain.
    """
    dom = (domain or "").lower()
    with get_session() as s:
        rows: List[Crawl] = (
            s.query(Crawl)
            .filter(Crawl.domain == dom, Crawl.visibility == "public")
            .order_by(Crawl.updated_at.desc())
            .limit(100)
            .all()
        )
    if not rows:
        raise HTTPException(status_code=404, detail="Not found")

    items = []
    for r in rows:
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

    # SEO meta for domain index
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"Public results for {dom} — {site_name}"
    meta_description = (
        f"Latest crawls for {dom}. View shareable Markdown, links, emails, and metrics."
    )
    abs_page_url = _abs_url(request, f"/domain/{dom}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    return templates.TemplateResponse(
        "domain_index.html",
        {
            "request": request,
            "domain": dom,
            "items": items,
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
    # Normalize inputs
    try:
        page = int(page)
    except Exception:
        page = 1
    page = 1 if page < 1 else page

    try:
        page_size = int(page_size)
    except Exception:
        page_size = 50
    page_size = max(10, min(100, page_size))

    dom = None
    if domain:
        dom = domain.strip().lower()
        if dom.startswith("www."):
            dom = dom[4:]

    allowed_status = {"pending", "running", "succeeded", "failed"}
    st = status if (status and status in allowed_status) else None

    offset = (page - 1) * page_size

    # Query rows
    with get_session() as s:
        q = s.query(Crawl).filter(Crawl.visibility == "public")
        if dom:
            q = q.filter(Crawl.domain == dom)
        if st:
            q = q.filter(Crawl.status == st)
        q = q.order_by(Crawl.updated_at.desc())
        rows: List[Crawl] = q.offset(offset).limit(page_size + 1).all()

    has_next = len(rows) > page_size
    rows = rows[:page_size]
    has_prev = page > 1

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

    # Prev/Next URLs (preserve filters)
    def _q(page_val: int) -> str:
        params = {}
        if dom:
            params["domain"] = dom
        if st:
            params["status"] = st
        params["page"] = str(page_val)
        params["page_size"] = str(page_size)
        return "/all?" + urlencode(params)

    prev_url = _q(page - 1) if has_prev else None
    next_url = _q(page + 1) if has_next else None

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
    # Canonical: keep domain/status; omit page & page_size when page == 1
    canonical_params = {}
    if dom:
        canonical_params["domain"] = dom
    if st:
        canonical_params["status"] = st
    canonical_path = "/all"
    if page > 1:
        canonical_params["page"] = str(page)
        canonical_params["page_size"] = str(page_size)
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
            "has_prev": has_prev,
            "has_next": has_next,
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
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    payload: Optional[dict] = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            payload = None

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
            "api_url": f"/api/analysis/private/{row.id}",
        },
    )
    # Prevent indexing of private results
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp
