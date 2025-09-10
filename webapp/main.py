import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib.resources import files as resource_files
from pathlib import Path
from typing import AsyncGenerator, Optional, Tuple, List, Dict
from urllib.parse import urlparse, parse_qsl, urlencode

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_

from markdownify_crawler.core import crawl as crawler_run
from webapp.db import get_session, init_db
from webapp.models import Crawl, Submission


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    init_db()
    yield
    # Shutdown (if needed)


app = FastAPI(title="Markdownify Web App", lifespan=lifespan)

# Templates directory (kept minimal, no CSS for MVP)
try:
    # When installed as a package, resolve templates from package data
    templates_dir = resource_files("webapp") / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
except Exception:
    # Fallback for dev runs
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# -----------------------------
# Helper configuration and request metadata utilities
# -----------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _normalize_ip(ip: str) -> str:
    ip = (ip or "").strip()
    if ip.startswith("::ffff:"):
        return ip[len("::ffff:") :]
    return ip


def _client_ip_from_request(request: Request, trust_proxy: bool) -> str:
    headers = request.headers
    ip = ""
    try:
        if trust_proxy:
            xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
            if xff:
                ip = xff.split(",")[0].strip()
        if not ip:
            ip = headers.get("x-real-ip") or headers.get("X-Real-IP") or ""
        if not ip:
            # Starlette provides client as (host, port)
            client = getattr(request, "client", None)
            if client and getattr(client, "host", None):
                ip = client.host
    except Exception:
        pass
    return _normalize_ip(ip)


def _hash_ip(ip: str, salt: str) -> str:
    s = f"{ip}|{salt}".encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def _collect_headers_subset(request: Request) -> dict:
    h = request.headers
    subset = {
        "user-agent": h.get("user-agent"),
        "accept-language": h.get("accept-language"),
        "referer": h.get("referer"),
        "origin": h.get("origin"),
        "host": h.get("host"),
        "x-request-id": h.get("x-request-id"),
        "x-correlation-id": h.get("x-correlation-id"),
        "x-forwarded-for": h.get("x-forwarded-for"),
        "x-real-ip": h.get("x-real-ip"),
    }
    # Drop None values
    return {k: v for k, v in subset.items() if v is not None}


def _get_secret_key() -> bytes:
    key = os.getenv("WEBAPP_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
    if not key:
        key = "dev-secret"
    return key.encode("utf-8")


def _make_csrf_token(session_id: str) -> str:
    ts = str(int(time.time()))
    data = f"{session_id}:{ts}:submit"
    mac = hmac.new(_get_secret_key(), data.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ts}:{mac}"


def _verify_csrf_token(
    token: Optional[str], session_id: str, max_age_seconds: int = 7200
) -> bool:
    try:
        if not token or not session_id:
            return False
        parts = token.split(":")
        if len(parts) != 2:
            return False
        ts_s, mac = parts[0], parts[1]
        ts = int(ts_s)
        if int(time.time()) - ts > int(max_age_seconds):
            return False
        expected = hmac.new(
            _get_secret_key(),
            f"{session_id}:{ts_s}:submit".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, mac)
    except Exception:
        return False


def normalize_domain(url: str) -> str:
    """
    Normalize domain: lowercase and strip leading 'www.'.
    """
    try:
        parsed = urlparse(url or "")
        host = (parsed.netloc or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _normalize_path(p: str) -> str:
    p = (p or "").strip()
    if not p or p == "":
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _normalize_query(q: str) -> str:
    """
    Parse query, sort by (key, value), preserve duplicates and blanks.
    Return normalized query string without leading '?'.
    """
    try:
        pairs = parse_qsl(q or "", keep_blank_values=True)
        pairs.sort(key=lambda kv: (kv[0], kv[1]))
        return urlencode(pairs, doseq=True)
    except Exception:
        return (q or "").lstrip("?").strip()


def canonicalize_url(url: str) -> Tuple[str, str, str, str]:
    """
    Return (domain, path, query, canonical_url)
    - domain: normalized by normalize_domain
    - path: normalized leading '/', trim trailing '/' except root
    - query: normalized sorted query string (no leading '?')
    - canonical_url: https://{domain}{path}{?query}
    """
    parsed = urlparse(url or "")
    domain = (parsed.netloc or "").lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    path = _normalize_path(parsed.path or "")
    query = _normalize_query(parsed.query or "")
    canon = f"https://{domain}{path}"
    if query:
        canon += f"?{query}"
    return domain, path, query, canon


def generate_short_key() -> str:
    """
    Generate a short, URL-safe key (~22 chars) from UUID4 bytes.
    """
    raw = uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def run_crawl_task(crawl_id: str, force_refresh: bool = False) -> None:
    """
    Background task: execute the crawl and persist results.
    """
    # Mark running and get URL in one session
    url = None
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return
        # If another worker already finished it, do nothing
        if row.status == "succeeded":
            return
        row.status = "running"
        row.updated_at = datetime.now(timezone.utc)
        url = row.url

    # Execute crawl
    try:
        payload = await crawler_run(
            url=url,
            crawl_internal=False,
            same_domain_only=True,
            include_emails=True,
            deobfuscate_emails=True,
            disable_cache=force_refresh,
            cache_dir=None,  # env MARKDOWNIFY_CACHE_DIR applies in core
        )
        payload_json = json.dumps(payload)
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.payload_json = payload_json
            row.status = "succeeded"
            row.error = None
            row.updated_at = datetime.now(timezone.utc)
    except Exception as e:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.status = "failed"
            row.error = str(e)
            row.updated_at = datetime.now(timezone.utc)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    Homepage: submission form and latest 10 public crawled URLs (domain+path+query).
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
        _make_csrf_token(session_id) if _env_bool("WEBAPP_CSRF_ENABLED", True) else ""
    )

    resp = templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "items": items,
            "csrf_token": csrf_token,
        },
    )
    if new_session:
        cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
        resp.set_cookie(
            key=cookie_name,
            value=session_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )
    return resp


@app.post("/submit")
async def submit(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    public: Optional[str] = Form(None),  # checkbox presence => public
    csrf_token: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
):
    """
    Handle form submission. Create/replace crawl row and start background job.
    Redirect:
      - public -> key page (/k/{key})
      - private -> private page (/private/{id})
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
                raise HTTPException(status_code=403, detail="Origin not allowed")
        elif referer_hdr:
            if _host_of(referer_hdr) != host_hdr:
                raise HTTPException(status_code=403, detail="Referer not allowed")

    # Honeypot field to deter bots
    if (website or "").strip():
        raise HTTPException(status_code=400, detail="Invalid submission")

    # CSRF validation
    if _env_bool("WEBAPP_CSRF_ENABLED", True):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
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
        from sqlalchemy import and_

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

    # Ensure anonymous session cookie exists
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    session_id = request.cookies.get(cookie_name)
    new_session = False
    if not session_id:
        session_id = str(uuid.uuid4())
        new_session = True

    # Build redirect response and set cookie if new
    if is_public:
        if not key_val:
            # Should not happen, but guard
            raise HTTPException(status_code=500, detail="Key generation failed")
        resp = RedirectResponse(url=f"/k/{key_val}", status_code=303)
    else:
        resp = RedirectResponse(url=f"/private/{crawl_id}", status_code=303)

    if new_session:
        cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
        # 365 days
        resp.set_cookie(
            key=cookie_name,
            value=session_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )

    return resp


@app.get("/k/{key}", response_class=HTMLResponse)
async def view_public_by_key(request: Request, key: str):
    """
    Public result page by short key.
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
        except Exception:
            payload = None

    return templates.TemplateResponse(
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
            "api_url": f"/api/k/{row.key}",
        },
    )


@app.get("/domain/{domain}", response_class=HTMLResponse)
async def view_domain_index(request: Request, domain: str):
    """
    List public results for a domain (not a single result).
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

    return templates.TemplateResponse(
        "domain_index.html",
        {
            "request": request,
            "domain": dom,
            "items": items,
        },
    )


@app.get("/private/{crawl_id}", response_class=HTMLResponse)
async def view_private(request: Request, crawl_id: str):
    """
    Private result page by UUID.
    """
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    payload: Optional[dict] = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except Exception:
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
            "api_url": f"/api/private/{row.id}",
        },
    )
    # Prevent indexing of private results
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@app.get("/api/k/{key}")
async def api_public_by_key(key: str):
    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.key == key, Crawl.visibility == "public")
            .one_or_none()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    if row.status != "succeeded" or not row.payload_json:
        return JSONResponse(
            content={
                "status": row.status,
                "domain": row.domain,
                "path": row.path,
                "query": row.query,
                "key": row.key,
            },
            status_code=202,
        )

    try:
        payload = json.loads(row.payload_json)
    except Exception:
        raise HTTPException(status_code=500, detail="Stored payload is invalid JSON")
    return JSONResponse(content=payload)


@app.get("/api/private/{crawl_id}")
async def api_private(crawl_id: str):
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    if row.status != "succeeded" or not row.payload_json:
        return JSONResponse(
            content={
                "status": row.status,
                "id": row.id,
                "domain": row.domain,
                "path": row.path,
                "query": row.query,
            },
            status_code=202,
        )

    try:
        payload = json.loads(row.payload_json)
    except Exception:
        raise HTTPException(status_code=500, detail="Stored payload is invalid JSON")
    resp = JSONResponse(content=payload)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@app.get("/api/domain/{domain}")
async def api_domain_index(domain: str):
    """
    Return list of public entries for a domain with their keys and status.
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
                "key": r.key,
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "status": r.status,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
            }
        )
    return JSONResponse(content={"domain": dom, "items": items})


@app.get("/api/status/{crawl_id}")
async def api_status(crawl_id: str):
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return JSONResponse(
        content={
            "id": row.id,
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "visibility": row.visibility,
            "status": row.status,
            "error": row.error,
            "updated_at": (row.updated_at or datetime.now(timezone.utc)).isoformat(),
        }
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}
