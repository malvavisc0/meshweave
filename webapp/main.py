import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.resources import files as resource_files
from pathlib import Path
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from markdownify_crawler.core import crawl as crawler_run
from webapp.db import get_session, init_db
from webapp.models import Crawl


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
    Homepage: submission form and latest 10 public crawled domains.
    """
    with get_session() as s:
        rows = (
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
                "title": title,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
                "status": r.status,
            }
        )

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "items": items,
        },
    )


@app.post("/submit")
async def submit(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    public: Optional[str] = Form(None),  # checkbox presence => public
):
    """
    Handle form submission. Create/replace crawl row and start background job.
    Redirect to domain or private page immediately.
    """
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=400, detail="Invalid URL. Must start with http(s)://"
        )

    is_public = bool(public)
    domain = normalize_domain(url)
    if not domain:
        raise HTTPException(status_code=400, detail="Unable to extract domain from URL")

    now = datetime.now(timezone.utc)

    # Upsert by domain and visibility
    visibility = "public" if is_public else "private"
    force_refresh = False
    with get_session() as s:
        existing = (
            s.query(Crawl)
            .filter(Crawl.domain == domain, Crawl.visibility == visibility)
            .one_or_none()
        )
        if existing:
            existing.url = url
            # If a run is already in progress, keep it running; else reset to pending
            if existing.status not in {"running"}:
                existing.status = "pending"
                existing.payload_json = None
                existing.error = None
            existing.updated_at = now
            crawl_id = existing.id
            # Treat repeated submissions for same domain+visibility as an update -> bypass cache
            force_refresh = True
        else:
            row = Crawl(
                url=url,
                domain=domain,
                visibility=visibility,
                status="pending",
                payload_json=None,
                error=None,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.flush()  # assign ID before commit at context exit
            crawl_id = row.id

    # Schedule background crawl if not already running
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if row and row.status in {"pending", "failed", "succeeded"}:
            # start a new run when pending/failed/succeeded
            background_tasks.add_task(run_crawl_task, crawl_id, force_refresh)

    if is_public:
        return RedirectResponse(url=f"/domain/{domain}", status_code=303)
    else:
        return RedirectResponse(url=f"/private/{crawl_id}", status_code=303)


@app.get("/domain/{domain}", response_class=HTMLResponse)
async def view_public_domain(request: Request, domain: str):
    """
    Public result page by domain.
    """
    dom = (domain or "").lower()
    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.domain == dom, Crawl.visibility == "public")
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
            "visibility": row.visibility,
            "status": row.status,
            "error": row.error,
            "payload": payload,
            "api_url": f"/api/domain/{row.domain}",
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


@app.get("/api/domain/{domain}")
async def api_public(domain: str):
    dom = (domain or "").lower()
    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.domain == dom, Crawl.visibility == "public")
            .one_or_none()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    if row.status != "succeeded" or not row.payload_json:
        return JSONResponse(
            content={"status": row.status, "domain": row.domain},
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
            content={"status": row.status, "id": row.id, "domain": row.domain},
            status_code=202,
        )

    try:
        payload = json.loads(row.payload_json)
    except Exception:
        raise HTTPException(status_code=500, detail="Stored payload is invalid JSON")
    resp = JSONResponse(content=payload)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


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
            "visibility": row.visibility,
            "status": row.status,
            "error": row.error,
            "updated_at": (row.updated_at or datetime.now(timezone.utc)).isoformat(),
        }
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}
