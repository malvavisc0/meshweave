import json
import logging
import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_

from webapp.db import get_session
from webapp.models import Crawl, Submission
from webapp.services.crawling import run_crawl_task
from webapp.services.site_crawling import run_site_crawl_task
from webapp.utils.config import _env_bool
from webapp.utils.http import _client_ip_from_request, _collect_headers_subset
from webapp.utils.logging import log_audit
from webapp.utils.metrics import homepage_analyze_submits
from webapp.utils.quotas import (
    enforce_concurrent_jobs_limit,
    enforce_daily_site_crawl_limit,
)
from webapp.utils.security import _hash_ip, _verify_csrf_token
from webapp.utils.url import canonicalize_url
from webapp.utils.visibility import resolve_page_visibility, resolve_site_visibility

router = APIRouter()


def _safe_return_target(return_to: str | None) -> str:
    target = (return_to or "").strip()
    if not target.startswith("/"):
        return "/"
    if target.startswith("//"):
        return "/"
    return target


def _cooldown_redirect(return_to: str | None) -> str:
    target = _safe_return_target(return_to)
    sep = "&" if "?" in target else "?"
    return f"{target}{sep}notice=cooldown"


def _cleanup_old_crawls(session, domain: str, visibility: str) -> None:
    """Delete oldest non-latest crawls beyond MAX_HISTORY_PER_DOMAIN limit."""
    max_history = int(os.getenv("MAX_HISTORY_PER_DOMAIN", "20"))
    old_rows = (
        session.query(Crawl)
        .filter(
            Crawl.domain == domain,
            Crawl.visibility == visibility,
            Crawl.is_latest == False,  # noqa: E712
        )
        .order_by(Crawl.created_at.desc())
        .offset(max_history)
        .all()
    )
    for old in old_rows:
        session.delete(old)


@router.post("/submit")
async def submit(
    request: Request,
    background_tasks: BackgroundTasks,
    # Unified form fields (page or site)
    mode: str | None = Form(None),
    url: str | None = Form(None),
    domain: str | None = Form(None),
    public: str | None = Form(None),  # checkbox presence => public (page mode only)
    return_to: str | None = Form(None),
    # Site optional limits
    max_pages: str | None = Form(None),
    max_depth: str | None = Form(None),
    time_budget_ms: str | None = Form(None),
    # Shared security
    csrf_token: str | None = Form(None),
    website: str | None = Form(None),  # honeypot
):
    """Unified submit handler for analyzing a page or crawling a site.

    Branches by mode ('page' or 'site') or by presence of 'domain' when mode is absent.
    """
    mode_val = (mode or "").strip().lower()

    # Honeypot field to deter bots (applies to both modes)
    if (website or "").strip():
        try:
            log_audit("honeypot_triggered", request=request, level=logging.WARNING)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Invalid submission")

    crawl_id: str | None = None

    # SITE MODE
    if mode_val == "site" or (domain and (not url or not (url or "").strip())):
        # CSRF validation (same as old /submit-site)
        if _env_bool("WEBAPP_CSRF_ENABLED", False):
            cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
            session_id = request.cookies.get(cookie_name) or ""
            max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
            if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
                return RedirectResponse(
                    url="/dashboard?notice=csrf_failed", status_code=303
                )

        user = getattr(request.state, "current_user", None)
        # Resolve visibility with override support
        visibility = resolve_site_visibility(bool(user), public)
        key = None

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

        # Derive start_url from the url field if it contains a path, else default to domain root
        _site_url = (url or "").strip()
        if _site_url and _site_url.startswith("http"):
            from webapp.utils.url import canonicalize_url as _canon

            _s_dom, _s_path, _s_query, _s_canon = _canon(_site_url)
            start_url = _s_canon
        else:
            start_url = f"https://{dom}/"

        # Upsert crawl row (unique on visibility+domain+path+query)
        now = datetime.now(UTC)
        with get_session() as s:
            existing = (
                s.query(Crawl)
                .filter(
                    Crawl.visibility == visibility,
                    Crawl.domain == dom,
                    Crawl.path == "/",
                    Crawl.query == "",
                    Crawl.is_latest == True,  # noqa: E712
                )
                .one_or_none()
            )
            if existing:
                # Guard against updating private rows owned by another user.
                can_update = True
                try:
                    ex_owner = getattr(existing, "user_id", None)
                    if ex_owner:
                        if (user and ex_owner != getattr(user, "id", None)) or (
                            not user
                        ):
                            can_update = False
                except Exception:
                    can_update = False

                if can_update:
                    # Retire old row and create new row for history tracking
                    existing.is_latest = False
                    old_key = getattr(existing, "key", None)
                    old_share_key = getattr(existing, "share_key", None)
                    existing.key = None
                    existing.share_key = None

                    # Generate new row using transferred keys
                    row = Crawl(
                        url=start_url,
                        domain=dom,
                        path="/",
                        query="",
                        canonical_url=start_url,
                        key=old_key,
                        share_key=old_share_key,
                        visibility=visibility,
                        status="pending",
                        payload_json=None,
                        error=None,
                        user_id=existing.user_id,
                        crawl_params=lim_req or {},
                        is_latest=True,
                        created_at=now,
                        updated_at=now,
                    )
                    s.add(row)
                    s.flush()
                    crawl_id = row.id
                    key = old_key

                    # Cleanup old history rows beyond limit
                    try:
                        _cleanup_old_crawls(s, dom, visibility)
                    except Exception:
                        pass
                    s.commit()
                else:
                    # Create a new row to avoid mutating another user's private crawl
                    if visibility == "public":
                        from webapp.utils.url import generate_short_key

                        key = generate_short_key()
                        dup = s.query(Crawl).filter(Crawl.key == key).one_or_none()
                        tries = 0
                        while dup is not None and tries < 3:
                            key = generate_short_key()
                            dup = s.query(Crawl).filter(Crawl.key == key).one_or_none()
                            tries += 1
                    row = Crawl(
                        url=start_url,
                        domain=dom,
                        path="/",
                        query="",
                        canonical_url=start_url,
                        key=key,
                        visibility=visibility,
                        status="pending",
                        payload_json=None,
                        error=None,
                        user_id=(getattr(user, "id", None) if user else None),
                        crawl_params=lim_req or {},
                        created_at=now,
                        updated_at=now,
                    )
                    s.add(row)
                    s.flush()
                    crawl_id = row.id
            else:
                # Generate a short key for public site crawls
                if visibility == "public":
                    from webapp.utils.url import generate_short_key

                    key = generate_short_key()
                    dup = s.query(Crawl).filter(Crawl.key == key).one_or_none()
                    tries = 0
                    while dup is not None and tries < 3:
                        key = generate_short_key()
                        dup = s.query(Crawl).filter(Crawl.key == key).one_or_none()
                        tries += 1
                row = Crawl(
                    url=start_url,
                    domain=dom,
                    path="/",
                    query="",
                    canonical_url=start_url,
                    key=key,
                    visibility=visibility,
                    status="pending",
                    payload_json=None,
                    error=None,
                    user_id=(getattr(user, "id", None) if user else None),
                    crawl_params=lim_req or {},
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                s.flush()
                crawl_id = row.id

        # Schedule site crawl (robust): BackgroundTasks + immediate task
        background_tasks.add_task(run_site_crawl_task, crawl_id, True)
        try:
            log_audit("site_crawl_enqueued", request=request, crawl_id=crawl_id)
        except Exception:
            pass

        # Redirect:
        if user and getattr(user, "id", None):
            return RedirectResponse(url=f"/analysis/{crawl_id}", status_code=303)
        elif visibility == "public" and key:
            # Anonymous public crawl → go straight to analysis page
            return RedirectResponse(url=f"/analysis/{key}", status_code=303)
        else:
            # Anonymous private: show submitted banner on home
            suffix = "&private=1" if visibility == "private" else ""
            return RedirectResponse(
                url=f"/?submitted={crawl_id}{suffix}", status_code=303
            )

    # PAGE MODE (default)
    # Normalize URL
    uval = (url or "").strip()
    if not (uval.startswith("http://") or uval.startswith("https://")):
        raise HTTPException(
            status_code=400, detail="Invalid URL. Must start with http(s)://"
        )

    user = getattr(request.state, "current_user", None)
    # Resolve is_public with override support
    is_public = resolve_page_visibility(bool(user), public)
    dom, path, query, canon_url = canonicalize_url(uval)
    if not dom:
        raise HTTPException(status_code=400, detail="Unable to extract domain from URL")

    # Metrics: count homepage Analyze submissions (page mode)
    try:
        homepage_analyze_submits.labels(
            authed="true" if user else "false",
            public="true" if is_public else "false",
        ).inc()
    except Exception:
        pass

    now = datetime.now(UTC)

    # Security: origin validation, honeypot already handled, CSRF, and simple rate limiting
    enforce_origin = _env_bool("WEBAPP_ENFORCE_ORIGIN", True)
    if enforce_origin:
        host_hdr = (request.headers.get("host") or "").lower()
        origin_hdr = request.headers.get("origin")
        referer_hdr = request.headers.get("referer")

        def _host_of(u: str | None) -> str:
            """Extract lowercase host from a URL-like string."""
            try:
                from urllib.parse import urlparse

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

    # Upsert behavior for page:
    # - public: upsert by (visibility, domain, path, query)
    # - private: create new or update only if safe to do so
    with get_session() as s:
        key_val: str | None = None

        if is_public:
            # Default all public analyses to site scope (domain root) so pages[] with markdown is available
            start_url = f"https://{dom}/"
            existing = (
                s.query(Crawl)
                .filter(
                    Crawl.visibility == "public",
                    Crawl.domain == dom,
                    Crawl.path == "/",
                    Crawl.query == "",
                    Crawl.is_latest == True,  # noqa: E712
                )
                .one_or_none()
            )
            if existing:
                # Enforce cooldown for public refresh
                refresh_min_age_minutes = int(
                    os.getenv("REFRESH_MIN_AGE_MINUTES", "60")
                )
                if now - existing.updated_at < timedelta(
                    minutes=refresh_min_age_minutes
                ):
                    return RedirectResponse(
                        url=_cooldown_redirect(return_to), status_code=303
                    )

                # Retire old row and create new row for history tracking
                existing.is_latest = False
                old_key = getattr(existing, "key", None)
                old_share_key = getattr(existing, "share_key", None)
                existing.key = None
                existing.share_key = None

                row = Crawl(
                    url=start_url,
                    domain=dom,
                    path="/",
                    query="",
                    canonical_url=start_url,
                    key=old_key,
                    share_key=old_share_key,
                    visibility="public",
                    status="pending",
                    payload_json=None,
                    error=None,
                    created_at=now,
                    updated_at=now,
                    is_latest=True,
                )
                s.add(row)
                s.flush()
                crawl_id = row.id
                key_val = old_key
                force_refresh = True

                # Cleanup old history rows beyond limit
                try:
                    _cleanup_old_crawls(s, dom, "public")
                except Exception:
                    pass
                s.commit()
            else:
                # Generate unique short key (retry on extremely rare collision)
                from webapp.utils.url import generate_short_key

                key_try = generate_short_key()
                dup = s.query(Crawl).filter(Crawl.key == key_try).one_or_none()
                tries = 0
                while dup is not None and tries < 3:
                    key_try = generate_short_key()
                    dup = s.query(Crawl).filter(Crawl.key == key_try).one_or_none()
                    tries += 1

                row = Crawl(
                    url=start_url,
                    domain=dom,
                    path="/",
                    query="",
                    canonical_url=start_url,
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
            existing = (
                s.query(Crawl)
                .filter(
                    Crawl.visibility == "private",
                    Crawl.domain == dom,
                    Crawl.path == path,
                    Crawl.query == query,
                    Crawl.is_latest == True,  # noqa: E712
                )
                .one_or_none()
            )
            if existing:
                # Enforce cooldown for public refresh
                refresh_min_age_minutes = int(
                    os.getenv("REFRESH_MIN_AGE_MINUTES", "60")
                )
                if now - existing.updated_at < timedelta(
                    minutes=refresh_min_age_minutes
                ):
                    return RedirectResponse(
                        url=_cooldown_redirect(return_to), status_code=303
                    )

                # Guard against updating private rows owned by another user.
                can_update = True
                try:
                    ex_owner = getattr(existing, "user_id", None)
                    if ex_owner:
                        if (user and ex_owner != getattr(user, "id", None)) or (
                            not user
                        ):
                            can_update = False
                except Exception:
                    can_update = False

                if can_update:
                    # Retire old row and create new row for history tracking
                    existing.is_latest = False
                    old_key = getattr(existing, "key", None)
                    old_share_key = getattr(existing, "share_key", None)
                    existing.key = None
                    existing.share_key = None

                    row = Crawl(
                        url=uval,
                        domain=dom,
                        path=path,
                        query=query,
                        canonical_url=canon_url,
                        key=old_key,
                        share_key=old_share_key,
                        visibility="private",
                        status="pending",
                        payload_json=None,
                        error=None,
                        user_id=existing.user_id,
                        created_at=now,
                        updated_at=now,
                        is_latest=True,
                    )
                    s.add(row)
                    s.flush()
                    crawl_id = row.id
                    force_refresh = True

                    # Cleanup old history rows beyond limit
                    try:
                        _cleanup_old_crawls(s, dom, "private")
                    except Exception:
                        pass
                    s.commit()
                else:
                    # Create a new private row for different owner/anonymous
                    row = Crawl(
                        url=uval,
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
            else:
                row = Crawl(
                    url=uval,
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

    if crawl_id is None:
        raise HTTPException(status_code=500, detail="Crawl creation failed")

    # Schedule background crawl if not already running
    with get_session() as s:
        existing_row = s.get(Crawl, crawl_id)
        if existing_row and existing_row.status in {"pending", "failed", "succeeded"}:
            # Public analyses: run site crawl to populate pages[] with markdown
            if is_public:
                background_tasks.add_task(run_site_crawl_task, crawl_id, force_refresh)
            else:
                # Private (or non-public) page analyses: run single-page crawl
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
        client_ip_hash = (
            _hash_ip(client_ip, ip_salt) if (client_ip and mask_ip) else None
        )
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
            status_row = s.get(Crawl, crawl_id)
            if status_row and status_row.status:
                status_at_submit = status_row.status

        # Persist submission log
        with get_session() as s:
            s.add(
                Submission(
                    crawl_id=crawl_id,
                    domain=dom,
                    url_at_submit=uval,
                    visibility=("public" if is_public else "private"),
                    force_refresh=force_refresh,
                    status_at_submit=status_at_submit,
                    client_ip=raw_client_ip if raw_client_ip else None,
                    client_ip_hash=(
                        client_ip_hash if _env_bool("WEBAPP_MASK_IP", False) else None
                    ),
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
                    headers_json=(
                        json.dumps(headers_subset) if headers_subset else None
                    ),
                    cookies_json=(json.dumps(cookies_obj) if cookies_obj else None),
                )
            )

    # Build redirect response and rotate session
    if is_public:
        if not key_val:
            # Should not happen, but guard
            raise HTTPException(status_code=500, detail="Key generation failed")
        resp = RedirectResponse(url=f"/analysis/{key_val}", status_code=303)
    else:
        if user and getattr(user, "id", None):
            resp = RedirectResponse(url=f"/analysis/{crawl_id}", status_code=303)
        else:
            # Anonymous private -> show banner on home; result is owner-restricted
            resp = RedirectResponse(
                url=f"/?submitted={crawl_id}&private=1", status_code=303
            )

    cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
    session_ttl = int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
    # Rotate session on every submit to reduce fixation risk
    new_session_id = __import__("uuid").uuid4().__str__()
    resp.set_cookie(
        key=cookie_name,
        value=new_session_id,
        max_age=session_ttl,
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
    )

    return resp
