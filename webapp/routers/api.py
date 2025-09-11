import json
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.logging import log_audit
from webapp.utils.url import _get_base_url

router = APIRouter()


@router.get("/api/k/{key}")
async def api_public_by_key(key: str):
    """Public API for a crawl addressed by short key.

    If the crawl is not yet succeeded, returns a 202 with status information; otherwise
    returns the stored payload.

    Args:
        key (str): Short key.

    Returns:
        JSONResponse: JSON payload or status info.

    Raises:
        HTTPException: 404 if not found; 500 if stored payload is invalid JSON.
    """
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
    except json.JSONDecodeError:
        try:
            log_audit("invalid_stored_payload", key=key, crawl_id=row.id)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
    return JSONResponse(content=payload)


@router.get("/api/private/{crawl_id}")
async def api_private(crawl_id: str):
    """Private API for a crawl addressed by UUID.

    If the crawl is not yet succeeded, returns a 202 with status information; otherwise
    returns the stored payload and sets noindex.

    Args:
        crawl_id (str): UUID of the crawl.

    Returns:
        JSONResponse: JSON payload or status info.
    """
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
    except json.JSONDecodeError:
        try:
            log_audit("invalid_stored_payload", crawl_id=row.id)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
    resp = JSONResponse(content=payload)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/api/domain/{domain}")
async def api_domain_index(domain: str):
    """List public entries for a domain with keys and status.

    Args:
        domain (str): Domain to query.

    Returns:
        JSONResponse: Object with domain and an array of items.
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


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    """Generate robots.txt content.

    Args:
        request (Request): Incoming request used to compute sitemap absolute URL.

    Returns:
        str: robots.txt content.
    """
    base = _get_base_url(request)
    return f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"


@router.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    """Generate a minimal sitemap.xml for public results.

    Args:
        request (Request): Incoming request used to compute absolute URLs.

    Returns:
        Response: XML response with urlset entries.
    """
    base = _get_base_url(request)
    with get_session() as s:
        rows: List[Crawl] = (
            s.query(Crawl)
            .filter(Crawl.visibility == "public")
            .order_by(Crawl.updated_at.desc())
            .limit(500)
            .all()
        )
    parts = []
    for r in rows:
        loc = f"{base}/k/{r.key}"
        lastmod = (r.updated_at or datetime.now(timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        parts.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(parts)
        + "\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/api/status/{crawl_id}")
async def api_status(crawl_id: str):
    """Return status information for a crawl id.

    Args:
        crawl_id (str): UUID of the crawl.

    Returns:
        JSONResponse: Object with crawl status and metadata.

    Raises:
        HTTPException: 404 if not found.
    """
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


@router.get("/healthz")
async def healthz():
    """Liveness probe endpoint.

    Returns:
        dict: {"ok": True}
    """
    return {"ok": True}
