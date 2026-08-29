import csv
import os
import re
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from webapp.db import get_session
from webapp.models import Crawl, Product
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.metrics import (
    homepage_advanced_toggle_clicks,
    homepage_signin_cta_clicks,
    metrics_body,
    metrics_content_type,
    result_share_clicks,
)
from webapp.utils.scoring import build_score_snapshot_context
from webapp.utils.times import ensure_utc
from webapp.utils.url import _get_base_url

router = APIRouter()


def _build_status_info(row: Crawl) -> dict:
    """Build the 202 status information dict for a not-ready crawl."""
    return {
        "status": row.status,
        "domain": row.domain,
        "path": row.path,
        "query": row.query,
        "key": row.key,
    }


def _preview_page(payload: dict) -> dict:
    """Return a minimal {title, description} page object from a payload.

    Site-scope payloads have no top-level ``page``; fall back to the first entry
    of ``pages`` when present.
    """
    page = payload.get("page") or {}
    if not page:
        pages = payload.get("pages") or []
        if isinstance(pages, list) and pages and isinstance(pages[0], dict):
            page = pages[0].get("page") or {}
    return {
        "title": (page.get("title") or "").strip(),
        "description": (page.get("description") or "").strip(),
    }


def _preview_counts(payload: dict) -> dict:
    """Return deliberately selected high-level counts from a payload."""
    pages = payload.get("pages")
    pages_count = (
        len(pages) if isinstance(pages, list) else (1 if payload.get("page") else 0)
    )
    emails = (payload.get("emails") or {}).get("counts") or {}
    links = payload.get("links") or {}
    return {
        "pages": pages_count,
        "emails": int(emails.get("total_unique") or 0),
        "internal_links": len(links.get("internal") or []),
        "external_links": len(links.get("external") or []),
    }


def _build_public_preview(row: Crawl) -> dict:
    """Build the curated public-preview object for a succeeded public crawl.

    Returns a brand-new dict built strictly from an allowlist of fields. It never
    returns the stored ``payload_json`` wholesale and never includes raw emails,
    email sources, full page objects, recommendation guidance, or the score
    snapshot.
    """
    payload = row.payload_json or {}
    ss = build_score_snapshot_context(row)

    scores: dict[str, dict[str, object]] = {}
    if ss is not None:
        for key, score_key, rating_key in (
            ("aeo", "aeo_score", "aeo_rating"),
            ("geo", "geo_score", "geo_rating"),
            ("aax", "aax_score", "aax_rating"),
        ):
            value = ss.get(score_key)
            if value is not None:
                scores[key] = {"composite": value, "rating": ss.get(rating_key)}

    risk_summary: dict[str, str | None] = {}
    finding_count = 0
    if ss is not None:
        interp = ss.get("interpretation") or {}
        risk_summary = {
            "profile_label": interp.get("profile_label"),
            "headline": interp.get("headline"),
            "diagnosis": interp.get("diagnosis"),
        }
        finding_count = len(ss.get("recommendations") or [])

    return {
        "domain": row.domain,
        "path": row.path,
        "canonical_url": row.canonical_url,
        "status": row.status,
        "page": _preview_page(payload),
        "scores": scores,
        "risk_summary": risk_summary,
        "finding_count": finding_count,
        "counts": _preview_counts(payload),
    }


@router.get("/api/analysis/public/{key}")
async def api_public_by_key(request: Request, key: str):
    """Public API for a crawl addressed by short key.

    Returns a curated public preview for every requester (authenticated or not).
    The preview is built from an explicit allowlist and never includes the stored
    payload, raw emails, page bodies, recommendation guidance, or the score
    snapshot. Owners with richer needs use the ownership-protected private API.

    If the crawl is not yet succeeded, returns a 202 with status information.
    """
    with get_session() as s:
        row = (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .filter(Crawl.key == key, Crawl.visibility == "public")
            .one_or_none()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if row.status != "succeeded" or not row.payload_json:
            return JSONResponse(content=_build_status_info(row), status_code=202)
        preview = _build_public_preview(row)
    return JSONResponse(content=preview)


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

    payload = row.payload_json or {}
    resp = JSONResponse(content=payload)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/api/domain/{domain}/history.csv", response_class=PlainTextResponse)
async def domain_history_csv(request: Request, domain: str):
    """Export per-domain score history as CSV.

    Auth required; user must own at least one crawl for that domain.
    Returns all succeeded crawls, oldest first, with per-run deltas.
    """
    user = await require_auth(request)
    dom = (domain or "").lower().strip()
    if not dom:
        raise HTTPException(status_code=400, detail="invalid_domain")

    with get_session() as s:
        crawls = (
            s.query(Crawl)
            .filter(
                Crawl.user_id == user.id,
                Crawl.domain == dom,
                Crawl.status == "succeeded",
            )
            .order_by(Crawl.updated_at.asc())
            .all()
        )
    if not crawls:
        raise HTTPException(status_code=404, detail="No data for this domain")

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["run", "date", "aeo", "aeo_delta", "geo", "geo_delta", "aax", "aax_delta"]
    )
    prev: Crawl | None = None
    for i, c in enumerate(crawls, 1):
        w.writerow(_csv_row(i, c, prev))
        prev = c

    headers = {"Content-Disposition": f'attachment; filename="{dom}-history.csv"'}
    return Response(content=buf.getvalue(), media_type="text/csv", headers=headers)


def _aax_composite(crawl: Crawl | None) -> float | None:
    """Return the aax composite score for a crawl, or None if unavailable."""
    if crawl and crawl.score_snapshot and crawl.score_snapshot.score_json:
        return cast(
            float | None,
            crawl.score_snapshot.score_json.get("aax", {}).get("composite"),
        )
    return None


def _csv_fmt(v) -> str:
    """Format a numeric value for CSV as a one-decimal string, or empty."""
    if v is None:
        return ""
    return str(round(float(v), 1))


def _csv_delta(cur, prev) -> str:
    """Format a one-decimal signed delta between two values, or empty."""
    if prev is None:
        return ""
    if cur is None or prev is None:
        return ""
    d = round(float(cur) - float(prev), 1)
    return ("+" if d > 0 else "") + str(d)


def _csv_row(i: int, cur: Crawl, prev: Crawl | None) -> list:
    """Build one history CSV row for run i, comparing cur against prev."""
    prev_aeo = prev.aeo_score if prev else None
    prev_geo = prev.geo_score if prev else None
    return [
        i,
        (cur.updated_at or datetime.now(UTC)).isoformat(),
        _csv_fmt(cur.aeo_score),
        _csv_delta(cur.aeo_score, prev_aeo),
        _csv_fmt(cur.geo_score),
        _csv_delta(cur.geo_score, prev_geo),
        _csv_fmt(_aax_composite(cur)),
        _csv_delta(_aax_composite(cur), _aax_composite(prev)),
    ]


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

    A dedicated allow section per AI crawler keeps their status "allowed"
    (the generic wildcard section carries path disallows that mark every
    bot "partially_restricted"). The private API paths remain disallowed
    for all agents — they are auth-protected and not content.

    Args:
        request (Request): Incoming request used to compute sitemap absolute URL.

    Returns:
        str: robots.txt content.
    """
    base = _get_base_url(request)
    ai_bots = (
        "GPTBot",
        "ChatGPT-User",
        "ClaudeBot",
        "Claude-Web",
        "anthropic-ai",
        "PerplexityBot",
        "Googlebot",
        "Google-Extended",
        "Bingbot",
        "cohere-ai",
        "Bytespider",
    )
    lines = []
    for bot in ai_bots:
        lines.append(f"User-agent: {bot}")
        lines.append("Allow: /")
        lines.append("")
    lines.extend(
        [
            "User-agent: *",
            "Allow: /",
            "Allow: /.well-known/",
            "Disallow: /api/analysis/private/",
            "Disallow: /api/status/",
            f"Sitemap: {base}/sitemap.xml",
        ]
    )
    return "\n".join(lines) + "\n"


@router.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    """Generate a minimal sitemap.xml for public results.

    Args:
        request (Request): Incoming request used to compute absolute URLs.

    Returns:
        Response: XML response with urlset entries.
    """
    base = _get_base_url(request)
    # Sitemaps list crawlable HTML pages only. robots.txt and llms*.txt are
    # crawler directives found by convention (and linked from robots.txt /
    # the page head), not content pages — listing them invites crawlers to
    # score infra files as thin content.
    static_urls = [
        (f"{base}/", datetime.now(UTC)),
        (f"{base}/browse", datetime.now(UTC)),
        (f"{base}/methodology", datetime.now(UTC)),
        (f"{base}/privacy", datetime.now(UTC)),
        (f"{base}/terms", datetime.now(UTC)),
        (f"{base}/contact", datetime.now(UTC)),
    ]
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
    for loc, updated_at in static_urls:
        lastmod = updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        parts.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
    for r in rows:
        loc = f"{base}/analysis/{r.key}"
        lastmod = (r.updated_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(parts)
        + "\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/api/analysis/public/{key}/summary")
async def api_public_summary(request: Request, key: str):
    """Curated public summary for a crawl addressed by short key.

    Returns the same curated public preview as ``GET /api/analysis/public/{key}``
    so this endpoint is not a broader public payload path. Returns a 202 with
    status information when the analysis is not yet succeeded.

    Args:
        request (Request): Incoming request (unused; boundary is the same for all).
        key (str): Short key that identifies the public crawl.

    Raises:
        HTTPException: 404 if the crawl is not found.
    """
    with get_session() as s:
        row = (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .filter(Crawl.key == key, Crawl.visibility == "public")
            .one_or_none()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if row.status != "succeeded" or not row.payload_json:
            return JSONResponse(content=_build_status_info(row), status_code=202)
        preview = _build_public_preview(row)
    return JSONResponse(content=preview)


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
        created = ensure_utc(getattr(row, "created_at", None) or now)
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
    """Limited processing status for a crawl id.

    Public analyses expose limited status to any requester. Private analyses are
    hidden from unauthenticated and non-owner requesters (404) so that the
    existence of a private UUID is never revealed. A private owner may see their
    own status. The short key and report URL of a private row are never returned
    to a non-owner.

    Args:
        request (Request): Incoming request used for the current user + base URL.
        crawl_id (str): UUID of the crawl.

    Returns:
        JSONResponse: Object with limited crawl status and metadata.

    Raises:
        HTTPException: 404 if not found, or if a private row is requested by a
            non-owner.
    """
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    current_user = getattr(request.state, "current_user", None)
    is_owner = bool(current_user and row.user_id and row.user_id == current_user.id)
    if row.visibility != "public" and not is_owner:
        raise HTTPException(status_code=404, detail="Not found")

    base = _get_base_url(request)
    if row.visibility == "public" and getattr(row, "key", None):
        report_url = f"{base}/analysis/{row.key}"
    else:
        report_url = f"{base}/analysis/{row.id}"

    content: dict[str, object] = {
        "id": row.id,
        "domain": row.domain,
        "path": row.path,
        "query": row.query,
        "status": row.status,
        "error": row.error,
        "updated_at": (row.updated_at or datetime.now(UTC)).isoformat(),
        "report_url": report_url,
    }
    if row.visibility == "public":
        content["key"] = row.key
    return JSONResponse(content=content)


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
    # Set WEBAPP_READINESS_REQUIRE_OAUTH=true to enforce this in environments where OAuth must be ready
    if _env_bool("WEBAPP_AUTH_ENABLED", True) and _env_bool(
        "WEBAPP_READINESS_REQUIRE_OAUTH", False
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


def _validate_product_fields(
    name: str, description: str, website: str, contact_info: str
) -> None:
    """Validate required product fields; raises 400 on failure.

    Raises:
        HTTPException: 400 when a required field is missing or contact_info
            is malformed.
    """
    if not name or not description or not website or not contact_info:
        raise HTTPException(
            status_code=400,
            detail="name, description, website and contact_info are required",
        )
    if not re.match(r"^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$", contact_info):
        raise HTTPException(status_code=400, detail="invalid_contact_info")


async def _product_fields(request: Request) -> tuple[str, str, str, str]:
    """Parse and validate product fields from the request body.

    Args:
        request (Request): Incoming request with the JSON product payload.

    Returns:
        tuple: (name, description, website, contact_info).

    Raises:
        HTTPException: 400 when a required field is missing or contact_info
            is malformed.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    website = (data.get("website") or "").strip()
    contact_info = (data.get("contact_info") or "").strip()

    _validate_product_fields(name, description, website, contact_info)
    return name, description, website, contact_info


@router.post("/api/products")
async def create_product(request: Request):
    """Create a product for the current user."""
    user = await require_auth(request)
    name, description, website, contact_info = await _product_fields(request)

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
    """Update a product owned by the current user."""
    user = await require_auth(request)
    name, description, website, contact_info = await _product_fields(request)

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
