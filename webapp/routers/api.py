import csv
import json
import os
import re
from datetime import UTC, datetime, timedelta
from io import StringIO

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from webapp.db import get_session
from webapp.models import Crawl, Product
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.logging import log_audit
from webapp.utils.metrics import (
    homepage_advanced_toggle_clicks,
    homepage_signin_cta_clicks,
    metrics_body,
    metrics_content_type,
    result_share_clicks,
)
from webapp.utils.url import _get_base_url, normalize_domain

router = APIRouter()


@router.get("/api/analysis/public/{key}")
async def api_public_by_key(request: Request, key: str):
    """Public API for a crawl addressed by short key.

    If the crawl is not yet succeeded, returns a 202 with status information; otherwise
    returns the stored payload.

    When the requester is not authenticated, email data is scrubbed (no addresses returned).
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
        return JSONResponse(
            status_code=500, content={"detail": "Internal Server Error"}
        )

    # Scrub emails for anonymous users (do not return any email addresses)
    try:
        current_user = getattr(request.state, "current_user", None)

        def _scrub_emails_recursive(obj):
            try:
                if isinstance(obj, dict):
                    for kk in list(obj.keys()):
                        lk = str(kk).lower()
                        if lk in ("emails", "emails_unique", "emails_by_url", "email"):
                            obj.pop(kk, None)
                            continue
                        _scrub_emails_recursive(obj.get(kk))
                elif isinstance(obj, list):
                    for it in obj:
                        _scrub_emails_recursive(it)
            except Exception:
                return

        if not current_user:
            em = payload.get("emails") or {}
            preserved = {
                "counts": em.get("counts") or {},
                "unique_count": len(em.get("unique") or []),
            }
            # Scrub everything else recursively
            for kk in list(payload.keys()):
                if kk == "emails":
                    continue
                _scrub_emails_recursive(payload.get(kk))
            payload["emails"] = preserved
    except Exception:
        try:
            payload.pop("emails", None)
        except Exception:
            pass

    return JSONResponse(content=payload)


@router.get("/api/analysis/private/{crawl_id}")
async def api_private(request: Request, crawl_id: str):
    """Private API for a crawl addressed by UUID.

    If the crawl is not yet succeeded, returns a 202 with status information; otherwise
    returns the stored payload and sets noindex.

    Args:
        crawl_id (str): UUID of the crawl.

    Returns:
        JSONResponse: JSON payload or status info.
    """
    await require_ownership(request, crawl_id)
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
        return JSONResponse(
            status_code=500, content={"detail": "Internal Server Error"}
        )
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
        rows: list[Crawl] = (
            s.query(Crawl)
            .filter(Crawl.domain == dom, Crawl.visibility == "public", Crawl.listed)
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
                "updated_at": (r.updated_at or datetime.now(UTC)).isoformat(),
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
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/analysis/private/\n"
        "Disallow: /api/status/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


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
        rows: list[Crawl] = (
            s.query(Crawl)
            .filter(
                Crawl.visibility == "public",
                Crawl.status == "succeeded",
                Crawl.listed,
            )
            .order_by(Crawl.updated_at.desc())
            .limit(500)
            .all()
        )
    parts = []
    for r in rows:
        loc = f"{base}/analysis/{r.key}"
        lastmod = (r.updated_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(parts)
        + "\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


def _load_public_row_by_key_or_404(key: str) -> Crawl:
    """Load a public Crawl row by short key or raise 404.

    Args:
        key (str): Short key that identifies a public crawl.

    Returns:
        Crawl: ORM row for the public crawl.

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
    return row


def _parse_payload_or_500(row: Crawl, key: str = "") -> dict:
    """Parse and return stored JSON payload or raise 500 on parse errors.

    Args:
        row (Crawl): Crawl row whose payload_json is parsed.
        key (str, optional): Optional key for audit logging context. Defaults to "".

    Returns:
        dict: Parsed JSON payload.

    Raises:
        HTTPException: 500 Internal Server Error if payload_json is invalid.
    """
    try:
        return json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        try:
            log_audit("invalid_stored_payload", key=key or row.key, crawl_id=row.id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/api/analysis/public/{key}/summary")
async def api_public_summary(key: str):
    """Computed summary for a public crawl by key.

    Args:
        key (str): Short key that identifies the public crawl.

    Returns:
        JSONResponse: Summary object with render/extraction metrics, links, emails,
        and SEO deltas when ready; or 202 status information when the analysis is
        not yet complete.

    Raises:
        HTTPException: 404 if the crawl is not found.
    """
    row = _load_public_row_by_key_or_404(key)
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

    payload = _parse_payload_or_500(row, key=key)

    # Extract fields safely
    pages_arr = payload.get("pages") or []
    page = payload.get("page") or {}
    if not page:
        try:
            if (
                isinstance(pages_arr, list)
                and len(pages_arr) > 0
                and isinstance(pages_arr[0], dict)
            ):
                page = pages_arr[0].get("page") or {}
        except Exception:
            page = {}
    og = page.get("og") or {}
    metrics = payload.get("metrics") or {}
    # Derive render metrics strictly from the first page (home "/")
    try:
        first_page_metrics = (
            (pages_arr[0].get("metrics") or {})
            if (
                isinstance(pages_arr, list)
                and len(pages_arr) > 0
                and isinstance(pages_arr[0], dict)
            )
            else {}
        )
    except Exception:
        first_page_metrics = {}
    render = first_page_metrics.get("render") or {}
    extraction = metrics.get("extraction") or {}
    links = payload.get("links") or {}
    emails = payload.get("emails") or {}

    base_domain = (extraction.get("base_domain") or row.domain or "").strip()
    is_site = (
        payload.get("scope") or getattr(row, "scope", "") or ""
    ).strip().lower() == "site"

    # Top external domains
    def _t(s):
        try:
            return (s or "").strip()
        except Exception:
            return ""

    top_ext: dict[str, int] = {}
    for u in links.get("external") or []:
        dom = normalize_domain(u)
        if not dom:
            continue
        top_ext[dom] = top_ext.get(dom, 0) + 1
    top_external_domains = [
        {"domain": d, "count": c}
        for d, c in sorted(top_ext.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # SEO deltas
    title_mismatch = _t(page.get("title")) != _t(og.get("title"))
    description_mismatch = _t(page.get("description")) != _t(og.get("description"))
    canonical_mismatch = _t(page.get("canonical")) != _t(row.canonical_url)
    og_missing = []
    for k in ("title", "description", "image", "url"):
        if not _t(og.get(k)):
            og_missing.append(k)

    summary = {
        "status": row.status,
        "domain": row.domain,
        "path": row.path,
        "query": row.query,
        "canonical_url": row.canonical_url,
        "page": {
            "title": page.get("title") or "",
            "description": page.get("description") or "",
            "og": {
                "title": og.get("title") or "",
                "description": og.get("description") or "",
                "image": og.get("image") or "",
                "url": og.get("url") or "",
            },
            "canonical": page.get("canonical") or "",
        },
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
            "counts": (emails.get("counts") or {}),
            "unique_count": len(emails.get("unique") or []),
        },
        "links": {
            "internal_count": len(links.get("internal") or []),
            "external_count": len(links.get("external") or []),
            "top_external_domains": top_external_domains,
        },
        "seo_deltas": {
            "title_mismatch": title_mismatch,
            "description_mismatch": description_mismatch,
            "canonical_mismatch": canonical_mismatch,
            "og_missing": og_missing,
        },
    }
    if is_site:
        try:
            summary["site"] = {
                "domain": row.domain,
                "canonical_url": row.canonical_url,
                "base_domain": base_domain,
            }
        except Exception:
            pass
        try:
            summary.pop("page", None)
        except Exception:
            pass
    return JSONResponse(content=summary)


@router.get("/api/analysis/public/{key}/emails.csv", response_class=PlainTextResponse)
async def api_public_emails_csv(request: Request, key: str):
    """Return unique emails for a public crawl as CSV.

    Args:
        request (Request): Incoming request for auth check.
        key (str): Short key that identifies the public crawl.

    Returns:
        Response: text/csv attachment with a single 'email' column.
    """
    await require_auth(request)
    row = _load_public_row_by_key_or_404(key)
    if row.status != "succeeded" or not row.payload_json:
        return JSONResponse(
            content={
                "status": row.status,
                "detail": "Analysis not ready",
            },
            status_code=202,
        )
    payload = _parse_payload_or_500(row, key=key)
    emails = (payload.get("emails") or {}).get("unique") or []
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["email"])
    for e in emails:
        w.writerow([e])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="emails-{key}.csv"'},
    )


@router.get("/api/analysis/public/{key}/links.csv", response_class=PlainTextResponse)
async def api_public_links_csv(key: str):
    """Return internal/external links for a public crawl as CSV.

    Args:
        key (str): Short key that identifies the public crawl.

    Returns:
        Response: text/csv attachment with columns: url, absolute_url, type, domain.
    """
    row = _load_public_row_by_key_or_404(key)
    if row.status != "succeeded" or not row.payload_json:
        return JSONResponse(
            content={
                "status": row.status,
                "detail": "Analysis not ready",
            },
            status_code=202,
        )
    payload = _parse_payload_or_500(row, key=key)
    metrics = payload.get("metrics") or {}
    extraction = metrics.get("extraction") or {}
    base_domain = (extraction.get("base_domain") or row.domain or "").strip()
    links = payload.get("links") or {}
    internal = links.get("internal") or []
    external = links.get("external") or []

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["url", "absolute_url", "type", "domain"])

    # Internal
    for u in internal:
        u = (u or "").strip()
        if not u:
            continue
        path = u if u.startswith("/") else f"/{u}"
        abs_u = f"https://{base_domain}{path}" if base_domain else path
        w.writerow([u, abs_u, "internal", base_domain])

    # External
    for u in external:
        u = (u or "").strip()
        if not u:
            continue
        dom = normalize_domain(u)
        w.writerow([u, u, "external", dom])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="links-{key}.csv"'},
    )


@router.get(
    "/api/analysis/public/{key}/top-external-domains.csv",
    response_class=PlainTextResponse,
)
async def api_public_top_domains_csv(key: str):
    """Return counts of external link domains for a public crawl as CSV.

    Args:
        key (str): Short key that identifies the public crawl.

    Returns:
        Response: text/csv attachment with columns: domain, count.
    """
    row = _load_public_row_by_key_or_404(key)
    if row.status != "succeeded" or not row.payload_json:
        return JSONResponse(
            content={
                "status": row.status,
                "detail": "Analysis not ready",
            },
            status_code=202,
        )
    payload = _parse_payload_or_500(row, key=key)
    links = payload.get("links") or {}
    external = links.get("external") or []
    counts: dict[str, int] = {}
    for u in external:
        dom = normalize_domain(u)
        if not dom:
            continue
        counts[dom] = counts.get(dom, 0) + 1

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["domain", "count"])
    for d, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        w.writerow([d, c])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="top-external-domains-{key}.csv"'
        },
    )


@router.post("/api/claim/public/{key}")
async def claim_public(request: Request, key: str):
    """Claim a public anonymous analysis by short key when eligible.

    Preconditions:
      - visibility='public'
      - user_id IS NULL
      - created_at <= now - CLAIM_PUBLIC_MIN_AGE_HOURS (default 24)
    Concurrency-safety: single UPDATE with conditions; 409 when already claimed.
    """
    user = await require_auth(request)

    try:
        min_age_hours = int(os.getenv("CLAIM_PUBLIC_MIN_AGE_HOURS", "24"))
    except Exception:
        min_age_hours = 24
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=min_age_hours)

    with get_session() as s:
        row = (
            s.query(Crawl)
            .filter(Crawl.key == key, Crawl.visibility == "public")
            .one_or_none()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Not found")

        # Fast checks (informative)
        if getattr(row, "user_id", None):
            return JSONResponse(status_code=409, content={"detail": "already_claimed"})
        created = getattr(row, "created_at", None) or now
        if created > cutoff:
            return JSONResponse(status_code=400, content={"detail": "ineligible"})

        # Concurrency-safe claim
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.key == key,
                Crawl.visibility == "public",
                Crawl.user_id.is_(None),
                Crawl.created_at <= cutoff,
            )
            .update({"user_id": user.id, "updated_at": now}, synchronize_session=False)
        )
        if updated != 1:
            return JSONResponse(status_code=409, content={"detail": "not_claimed"})

        # Return claimed id
        claimed = s.query(Crawl).filter(Crawl.key == key).one_or_none()
        return {"ok": True, "id": getattr(claimed, "id", None), "key": key}


@router.get("/api/status/{crawl_id}")
async def api_status(request: Request, crawl_id: str):
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
    base = _get_base_url(request)
    report_path = (
        f"/analysis/{row.key}"
        if (row.visibility == "public" and getattr(row, "key", None))
        else f"/analysis/{row.id}"
    )
    return JSONResponse(
        content={
            "id": row.id,
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "visibility": row.visibility,
            "status": row.status,
            "error": row.error,
            "updated_at": (row.updated_at or datetime.now(UTC)).isoformat(),
            "key": getattr(row, "key", None),
            "report_url": f"{base}{report_path}",
        }
    )


@router.get("/readyz")
async def readyz():
    """Readiness probe: DB connectivity and auth config (when enabled).

    Returns:
        dict | JSONResponse: {"ok": True} when ready; otherwise a 503 JSON response
        with a 'reason' field explaining the failure.
    """
    # DB connectivity
    try:
        with get_session() as s:
            # Lightweight check
            _ = s.execute(text("SELECT 1")).scalar()
    except Exception:
        return JSONResponse(
            status_code=503, content={"ok": False, "reason": "db_unavailable"}
        )
    # Auth config when enabled and explicitly required
    # Set WEBAPP_READYZ_REQUIRE_AUTH=true to enforce this in environments where OAuth must be ready
    if _env_bool("WEBAPP_AUTH_ENABLED", True) and _env_bool(
        "WEBAPP_READYZ_REQUIRE_AUTH", False
    ):
        if not (os.getenv("OAUTH_CLIENT_ID") and os.getenv("OAUTH_CLIENT_SECRET")):
            return JSONResponse(
                status_code=503, content={"ok": False, "reason": "auth_config_missing"}
            )
    return {"ok": True}


@router.get("/metrics")
async def metrics():
    """Prometheus metrics exposition.

    Returns:
        Response: Metrics body in Prometheus exposition format and content type.
    """
    return Response(content=metrics_body(), media_type=metrics_content_type())


@router.get("/api/track")
async def track_get(event: str = "", action: str = "", type: str = ""):
    """Lightweight tracking endpoint (GET) for client beacons."""
    try:
        if event == "advanced_toggle" and action in ("open", "close"):
            homepage_advanced_toggle_clicks.labels(action=action).inc()
        elif event == "signin_click":
            homepage_signin_cta_clicks.inc()
        elif event == "share_click" and type in ("copy", "link", "other"):
            result_share_clicks.labels(type=type).inc()
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/track")
async def track_post(event: str = "", action: str = "", type: str = ""):
    """Lightweight tracking endpoint (POST) for client beacons."""
    try:
        if event == "advanced_toggle" and action in ("open", "close"):
            homepage_advanced_toggle_clicks.labels(action=action).inc()
        elif event == "signin_click":
            homepage_signin_cta_clicks.inc()
        elif event == "share_click" and type in ("copy", "link", "other"):
            result_share_clicks.labels(type=type).inc()
    except Exception:
        pass
    return {"ok": True}


@router.get("/healthz")
async def healthz():
    """Liveness probe endpoint.

    Returns:
        dict: {"ok": True}
    """
    return {"ok": True}


# ===== Products API (functional, minimal fields) =====


def _product_to_dict(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.name or "",
        "description": p.description or "",
        "website": p.website or None,
        "contact_info": p.contact_info or None,
        "created_at": (p.created_at or datetime.now(UTC)).isoformat(),
        "updated_at": (p.updated_at or datetime.now(UTC)).isoformat(),
    }


@router.get("/api/products")
async def list_products(request: Request):
    user = await require_auth(request)
    with get_session() as s:
        rows = (
            s.query(Product)
            .filter(Product.user_id == user.id)
            .order_by(Product.updated_at.desc())
            .all()
        )
    items = [_product_to_dict(p) for p in rows]
    return {"items": items}


@router.post("/api/products")
async def create_product(request: Request):
    user = await require_auth(request)
    try:
        data = await request.json()
    except Exception:
        data = {}

    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    website = (data.get("website") or "").strip()
    contact_info = (data.get("contact_info") or "").strip()

    if not name or not description or not website or not contact_info:
        raise HTTPException(
            status_code=400,
            detail="name, description, website and contact_info are required",
        )
    if not re.match(r"^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$", contact_info):
        raise HTTPException(status_code=400, detail="invalid_contact_info")

    with get_session() as s:
        # Enforce unique per user (by model constraint)
        p = Product(
            user_id=user.id,
            name=name,
            description=description,
            website=website,
            contact_info=contact_info,
        )
        s.add(p)
        try:
            s.flush()
        except IntegrityError:
            # Unique constraint (user_id, name) violated
            raise HTTPException(status_code=409, detail="duplicate_name")
        return JSONResponse(status_code=201, content={"item": _product_to_dict(p)})


@router.put("/api/products/{product_id}")
async def update_product(request: Request, product_id: str):
    user = await require_auth(request)
    try:
        data = await request.json()
    except Exception:
        data = {}

    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    website = (data.get("website") or "").strip()
    contact_info = (data.get("contact_info") or "").strip()

    if not name or not description or not website or not contact_info:
        raise HTTPException(
            status_code=400,
            detail="name, description, website and contact_info are required",
        )
    if not re.match(r"^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$", contact_info):
        raise HTTPException(status_code=400, detail="invalid_contact_info")

    now = datetime.now(UTC)
    with get_session() as s:
        row = (
            s.query(Product)
            .filter(Product.id == product_id, Product.user_id == user.id)
            .one_or_none()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Not found")

        row.name = name
        row.description = description
        row.website = website
        row.contact_info = contact_info
        row.updated_at = now

        try:
            s.flush()
        except IntegrityError:
            # Unique constraint on (user_id, name) when renaming to an existing product name
            raise HTTPException(status_code=409, detail="duplicate_name")
        return {"item": _product_to_dict(row)}
