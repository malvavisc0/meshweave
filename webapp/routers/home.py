import json
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import case
from sqlalchemy.orm import Session

from webapp.db import get_db
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.config import _env_bool
from webapp.utils.logging import log_audit
from webapp.utils.security import _make_csrf_token
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/.well-known/llms.txt", response_class=PlainTextResponse)
async def llms_txt(request: Request):
    base = _abs_url(request, "")
    return (
        f"Site: {base}\n"
        "Product: MeshWeave\n"
        "Summary: AI visibility risk analysis for citation, discovery, and agent trust.\n"
        "Primary actions: Run an analysis, review recommendations, contact an expert.\n"
        f"Sitemap: {base}/sitemap.xml\n"
        f"Full: {base}/.well-known/llms-full.txt\n"
    )


@router.get("/.well-known/llms-full.txt", response_class=PlainTextResponse)
async def llms_full_txt(request: Request):
    base = _abs_url(request, "")
    return (
        "# MeshWeave\n\n"
        f"Base URL: {base}\n\n"
        "MeshWeave audits how AI systems crawl, understand, and cite websites. "
        "It identifies technical weaknesses that limit visibility, citation confidence, "
        "generative discovery, and AI agent trust.\n\n"
        "## Key pages\n"
        f"- Homepage: {base}/\n"
        f"- Browse analyses: {base}/browse\n"
        f"- Products: {base}/products\n"
        f"- Methodology: {base}/methodology\n"
        f"- Privacy: {base}/privacy\n"
        f"- Terms: {base}/terms\n\n"
        "## Core capabilities\n"
        "- AI visibility risk analysis\n"
        "- Citation-readiness diagnostics\n"
        "- Entity consistency and crawl-access auditing\n"
        "- Prioritized remediation roadmap\n\n"
        "## Contact\n"
        f"- Email: {os.getenv('FOOTER_CONTACT_EMAIL', 'hello@meshweave.com').strip()}\n"
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    """Homepage with submission form and latest public results.

    Implements:
      - Community metrics banner (lifetime totals) computed inline per request (no cache)
      - Recent analyses: max 9 items, status-ranked (succeeded, running, others), then updated_at DESC
      - Bulk email/page counts, relative time strings, and optional site summary snippet
    """

    def _safe_json_load(s: str):
        try:
            return json.loads(s) if s else None
        except Exception:
            return None

    def _first_sentence(text: str, limit: int = 160) -> str:
        try:
            t = (text or "").strip()
            if not t:
                return ""
            # Split on sentence end or newline
            for sep in [". ", "। ", "。", "…", "\n"]:
                if sep in t:
                    t = t.split(sep, 1)[0]
                    break
            return (t[: limit - 1] + "…") if len(t) > limit else t
        except Exception:
            return ""

    def _relative_time(dt: datetime) -> str:
        try:
            now = datetime.now(UTC)
            base = dt or now
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            diff = now - base
            secs = int(max(0, diff.total_seconds()))
            if secs < 60:
                return f"{secs}s ago"
            mins = secs // 60
            if mins < 60:
                return f"{mins}m ago"
            hrs = mins // 60
            if hrs < 24:
                return f"{hrs}h ago"
            days = hrs // 24
            return f"{days}d ago"
        except Exception:
            return ""

    # Query latest public crawls with status ranking and limit 9
    from sqlalchemy.orm import joinedload

    rows: list[Crawl] = (
        db.query(Crawl)
        .options(joinedload(Crawl.score_snapshot))
        .filter(Crawl.visibility == "public", Crawl.user_id.is_(None), Crawl.listed, Crawl.key.is_not(None))
        .order_by(
            case(
                (Crawl.status == "succeeded", 0),
                (Crawl.status == "running", 1),
                else_=2,
            ),
            Crawl.updated_at.desc(),
        )
        .limit(5)
        .all()
    )

    ids = [r.id for r in rows] or ["-"]

    # Bulk counts from payload_json (CrawlLink/CrawlEmail tables removed)
    email_counts_map: dict[str, int] = {}
    page_counts_map: dict[str, int] = {}

    if ids and len(rows) > 0:
        for r in rows:
            try:
                p = r.payload_json or {} if r.payload_json else {}
                if isinstance(p, dict):
                    # Email count from payload
                    emails_data = p.get("emails") or {}
                    unique_emails = emails_data.get("unique") or []
                    email_counts_map[r.id] = len(unique_emails)
                    # Page count from payload (number of pages in site crawl)
                    pages_list = p.get("pages") or []
                    if isinstance(pages_list, list):
                        page_counts_map[r.id] = len(pages_list)
                    else:
                        page_counts_map[r.id] = 0
            except Exception:
                email_counts_map[r.id] = 0
                page_counts_map[r.id] = 0

    # Count runs per domain for "X Runs" badge
    from collections import Counter

    domain_run_counts: Counter[str] = Counter()
    for r in rows:
        if r.domain:
            domain_run_counts[r.domain] += 1

    # Build item payloads
    items = []
    now = datetime.now(UTC)
    for r in rows:
        payload = (
            r.payload_json
            if isinstance(r.payload_json, dict)
            else _safe_json_load(r.payload_json or "")
        )
        title = ""
        description = ""
        og_desc = ""
        try:
            if isinstance(payload, dict):
                if r.crawl_params:
                    # For site crawls, title from first page
                    pages = payload.get("pages") or []
                    if pages and isinstance(pages, list) and len(pages) > 0:
                        pg = pages[0].get("page") or {}
                        title = (pg.get("title") or "").strip()
                        description = (pg.get("description") or "").strip()
                        og_desc = (
                            (pg.get("og") or {}).get("description") or ""
                        ).strip()
                else:
                    # For page crawls
                    pg = payload.get("page") or {}
                    title = (pg.get("title") or "").strip()
                    description = (pg.get("description") or "").strip()
                    og_desc = ((pg.get("og") or {}).get("description") or "").strip()
        except Exception:
            pass

        updated_dt = r.updated_at or now
        try:
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=UTC)
        except Exception:
            updated_dt = now
        updated_iso = updated_dt.isoformat()
        updated_relative = _relative_time(updated_dt)
        is_new = (now - updated_dt).total_seconds() <= 2 * 3600

        summary_snippet = ""
        if bool(r.crawl_params):
            if description:
                summary_snippet = _first_sentence(description, 160)
            elif og_desc:
                summary_snippet = _first_sentence(og_desc, 160)
            else:
                try:
                    md = (payload or {}).get("markdown") or ""
                except Exception:
                    md = ""
                summary_snippet = _first_sentence(md, 160)

        # Extract scores
        aeo_sc = r.aeo_score
        geo_sc = r.geo_score
        aeo_rt = r.aeo_rating
        geo_rt = r.geo_rating
        aax_sc = None
        try:
            snap = r.score_snapshot
            if snap and snap.score_json:
                aax_data = snap.score_json.get("aax") or {}
                aax_sc = aax_data.get("composite")
        except Exception:
            pass

        items.append(
            {
                "key": r.key,
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "title": title or r.canonical_url or f"{r.domain}{r.path or ''}",
                "scope": "site" if r.crawl_params else "page",
                "status": r.status,
                "page_count": page_counts_map.get(r.id, 0),
                "email_count": email_counts_map.get(r.id, 0),
                "aeo_score": aeo_sc,
                "geo_score": geo_sc,
                "aax_score": aax_sc,
                "aeo_rating": aeo_rt,
                "geo_rating": geo_rt,
                "updated_iso": updated_iso,
                "updated_relative": updated_relative,
                "is_new": bool(is_new),
                "summary_snippet": (summary_snippet if bool(r.crawl_params) else ""),
                "run_count": domain_run_counts.get(r.domain, 1),
                # Back-compat fields (legacy templates)
                "updated_at": updated_iso,
            }
        )

    # Community metrics (lifetime totals) computed inline (no caching)
    # Base filter mirrors /browse: public, anonymous, listed, keyed, succeeded
    _base = [
        Crawl.visibility == "public",
        Crawl.status == "succeeded",
        Crawl.user_id.is_(None),
        Crawl.listed,
        Crawl.key.is_not(None),
    ]
    try:
        analyses_total = db.query(Crawl).filter(*_base).count() or 0

        # Unique domains scored (must have actual scores)
        domains_total = (
            db.query(Crawl.domain)
            .filter(*_base, Crawl.aeo_score.is_not(None))
            .distinct()
            .count()
        ) or 0

        # Compute community metrics from payload_json
        # Deduplicate by domain: only count pages from the latest
        # succeeded run per domain to avoid double-counting
        pages_total = 0
        public_crawls = (
            db.query(
                Crawl.domain,
                Crawl.payload_json,
                Crawl.crawl_params,
                Crawl.updated_at,
            )
            .filter(*_base)
            .all()
        )
        # Group by domain, keep only the latest per domain
        latest_by_domain: dict[str, tuple] = {}
        for dom, pj, cp, ua in public_crawls:
            if dom not in latest_by_domain or (
                ua and ua > (latest_by_domain[dom][2] or datetime.min)
            ):
                latest_by_domain[dom] = (pj, cp, ua)
        for pj, cp, _ua in latest_by_domain.values():
            try:
                p = (
                    pj
                    if isinstance(pj, dict)
                    else (json.loads(pj) if isinstance(pj, str) else {})
                )
                if not isinstance(p, dict):
                    continue
                # Pages count: site crawls have a "pages" array,
                # page crawls evaluate 1 page
                if cp:  # site crawl
                    pages_list = p.get("pages") or []
                    if isinstance(pages_list, list):
                        pages_total += len(pages_list)
                else:  # page crawl
                    pages_total += 1
            except Exception:
                pass
        community_metrics = {
            "analyses_total": int(analyses_total),
            "domains_total": int(domains_total),
            "pages_total": int(pages_total),
        }
    except Exception:
        community_metrics = None

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

    # SEO meta for home (LLM-first)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"{site_name} — AI Visibility Risk Analysis"
    meta_description = (
        "Identify the structural weaknesses limiting citation, discovery, "
        "and AI agent trust. MeshWeave audits how AI systems crawl, "
        "understand, and recommend your site."
    )
    abs_page_url = _abs_url(request, "/")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: SoftwareApplication (include once via base template)
    try:
        json_ld_dict = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": site_name,
            "applicationCategory": "DataExtraction",
            "url": abs_page_url,
            "softwareVersion": (os.getenv("APP_VERSION", "v1") or "v1"),
            "provider": {"@type": "Organization", "name": site_name},
            "description": (
                "MeshWeave audits how AI systems crawl, understand, and cite "
                "websites, then identifies the technical weaknesses that "
                "limit visibility and trust."
            ),
            "featureList": [
                "AI visibility risk analysis",
                "Citation-readiness diagnostics",
                "Entity consistency and crawl-access auditing",
                "Prioritized remediation roadmap",
            ],
            "termsOfService": _abs_url(request, "/terms"),
            "privacyPolicy": _abs_url(request, "/privacy"),
            "isAccessibleForFree": True,
        }
        json_ld = json.dumps(json_ld_dict)
    except Exception:
        json_ld = None

    # Optional banner when a crawl was just started (anonymous redirect target)
    submitted_id = request.query_params.get("submitted") or None
    submitted_status_url = f"/api/status/{submitted_id}" if submitted_id else None
    submitted_is_private = True if request.query_params.get("private") else False

    resp = templates.TemplateResponse(
        request,
        "home.html",
        {
            "items": items,
            "community_metrics": community_metrics,
            "csrf_token": csrf_token,
            "login_error": True if request.query_params.get("error") else False,
            "notice": request.query_params.get("notice") or None,
            # Submission banner
            "submitted_id": submitted_id,
            "submitted_status_url": submitted_status_url,
            "submitted_is_private": submitted_is_private,
            # SEO
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "json_ld": json_ld,
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
