import json
import os
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import case
from sqlalchemy.orm import Session, joinedload

from meshweave.scoring.interpretation import interpret_profile
from webapp.db import get_db
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.config import _env_bool
from webapp.utils.logging import log_audit
from webapp.utils.security import _make_csrf_token, set_csrf_session_cookie
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/.well-known/llms.txt", response_class=PlainTextResponse)
async def llms_txt(request: Request):
    from pathlib import Path

    static_path = (
        Path(__file__).resolve().parent.parent / "static" / ".well-known" / "llms.txt"
    )
    try:
        return static_path.read_text()
    except FileNotFoundError:
        base = _abs_url(request, "")
        return (
            f"Site: {base}\n"
            "Product: MeshWeave\n"
            "Summary: AI visibility risk analysis for citation, discovery, and agent trust.\n"
        )


@router.get(
    "/.well-known/llms-full.txt",
    response_class=PlainTextResponse,
)
async def llms_full_txt(request: Request):
    from pathlib import Path

    static_path = (
        Path(__file__).resolve().parent.parent
        / "static"
        / ".well-known"
        / "llms-full.txt"
    )
    try:
        return static_path.read_text()
    except FileNotFoundError:
        return "# MeshWeave\n"


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


def _fetch_recent_rows(db: Session) -> list[Crawl]:
    return (
        db.query(Crawl)
        .options(joinedload(Crawl.score_snapshot))
        .filter(
            Crawl.visibility == "public",
            Crawl.user_id.is_(None),
            Crawl.listed,
            Crawl.key.is_not(None),
        )
        .order_by(
            case(
                (Crawl.status == "succeeded", 0),
                (Crawl.status == "running", 1),
                else_=2,
            ),
            Crawl.updated_at.desc(),
        )
        .limit(6)
        .all()
    )


def _build_count_maps(
    rows: list[Crawl],
) -> tuple[dict[str, int], dict[str, int]]:
    email_counts_map: dict[str, int] = {}
    page_counts_map: dict[str, int] = {}
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
    return email_counts_map, page_counts_map


def _count_runs_per_domain(rows: list[Crawl]) -> Counter[str]:
    domain_run_counts: Counter[str] = Counter()
    for r in rows:
        if r.domain:
            domain_run_counts[r.domain] += 1
    return domain_run_counts


def _meta_from_page(pg: dict) -> tuple[str, str, str]:
    """Extract (title, description, og_desc) fields from a page dict."""
    title = (pg.get("title") or "").strip()
    description = (pg.get("description") or "").strip()
    og_desc = ((pg.get("og") or {}).get("description") or "").strip()
    return title, description, og_desc


def _meta_page_source(payload: dict, crawl_params: Any) -> dict | None:
    """Locate the page dict holding meta fields, or None when absent."""
    if crawl_params:
        # For site crawls, title from first page
        pages = payload.get("pages") or []
        if pages and isinstance(pages, list) and len(pages) > 0:
            return pages[0].get("page") or {}
        return None
    # For page crawls
    return payload.get("page") or {}


def _extract_meta(payload: Any, crawl_params: Any) -> tuple[str, str, str]:
    """Extract (title, description, og_desc) from a crawl payload."""
    title = ""
    description = ""
    og_desc = ""
    try:
        if isinstance(payload, dict):
            pg = _meta_page_source(payload, crawl_params)
            if pg is not None:
                title, description, og_desc = _meta_from_page(pg)
    except Exception:
        pass
    return title, description, og_desc


def _coerce_aware(dt: datetime, now: datetime) -> datetime:
    try:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
    except Exception:
        return now
    return dt


def _build_summary(
    payload: Any,
    description: str,
    og_desc: str,
    crawl_params: Any,
) -> str:
    summary_snippet = ""
    if bool(crawl_params):
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
    return summary_snippet


def _extract_scores(
    r: Crawl,
) -> tuple[Any, Any, Any, Any, Any, Any]:
    aeo_sc = r.aeo_score
    geo_sc = r.geo_score
    aeo_rt = r.aeo_rating
    geo_rt = r.geo_rating
    aax_sc = None
    aax_rt = None
    try:
        snap = r.score_snapshot
        if snap and snap.score_json:
            aax_data = snap.score_json.get("aax") or {}
            aax_sc = aax_data.get("composite")
            aax_rt = aax_data.get("rating")
    except Exception:
        pass
    return aeo_sc, geo_sc, aeo_rt, geo_rt, aax_sc, aax_rt


def _interpret_headline(aeo_sc: Any, geo_sc: Any, aax_sc: Any) -> tuple[Any, Any]:
    headline = None
    tone = None
    try:
        if aeo_sc is not None and geo_sc is not None and aax_sc is not None:
            interp = interpret_profile(aeo_sc, geo_sc, aax_sc, score_basis="auto")
            headline = interp.get("headline")
            tone = interp.get("tone")
    except Exception:
        pass
    return headline, tone


def _serialize_row(
    r: Crawl,
    page_counts_map: dict[str, int],
    email_counts_map: dict[str, int],
    domain_run_counts: Counter[str],
    now: datetime,
) -> dict:
    payload = (
        r.payload_json
        if isinstance(r.payload_json, dict)
        else _safe_json_load(r.payload_json or "")
    )
    title, description, og_desc = _extract_meta(payload, r.crawl_params)
    updated_dt = _coerce_aware(r.updated_at or now, now)
    updated_iso = updated_dt.isoformat()
    updated_relative = _relative_time(updated_dt)
    is_new = (now - updated_dt).total_seconds() <= 2 * 3600
    summary_snippet = _build_summary(payload, description, og_desc, r.crawl_params)
    aeo_sc, geo_sc, aeo_rt, geo_rt, aax_sc, aax_rt = _extract_scores(r)
    headline, tone = _interpret_headline(aeo_sc, geo_sc, aax_sc)
    return {
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
        "aax_rating": aax_rt,
        "updated_iso": updated_iso,
        "updated_relative": updated_relative,
        "is_new": bool(is_new),
        "summary_snippet": (summary_snippet if bool(r.crawl_params) else ""),
        "run_count": domain_run_counts.get(r.domain, 1),
        "headline": headline,
        "tone": tone,
        # Back-compat fields (legacy templates)
        "updated_at": updated_iso,
    }


def _latest_by_domain(public_crawls: list) -> dict[str, tuple]:
    """Group public crawl tuples by domain, keeping the latest per domain."""
    latest_by_domain: dict[str, tuple] = {}
    for dom, pj, cp, ua in public_crawls:
        if dom not in latest_by_domain or (
            ua and ua > (latest_by_domain[dom][2] or datetime.min)
        ):
            latest_by_domain[dom] = (pj, cp, ua)
    return latest_by_domain


def _crawl_page_count(pj: Any, cp: Any) -> int:
    """Count pages for one crawl payload (site: len(pages); page: 1)."""
    try:
        p = (
            pj
            if isinstance(pj, dict)
            else (json.loads(pj) if isinstance(pj, str) else {})
        )
        if not isinstance(p, dict):
            return 0
        # Pages count: site crawls have a "pages" array,
        # page crawls evaluate 1 page
        if cp:  # site crawl
            pages_list = p.get("pages") or []
            return len(pages_list) if isinstance(pages_list, list) else 0
        return 1  # page crawl
    except Exception:
        return 0


def _count_public_pages(db: Session, base_filters: list) -> int:
    """Count pages across the latest succeeded public run per domain."""
    public_crawls = (
        db.query(
            Crawl.domain,
            Crawl.payload_json,
            Crawl.crawl_params,
            Crawl.updated_at,
        )
        .filter(*base_filters)
        .all()
    )
    pages_total = 0
    # Group by domain, keep only the latest per domain
    for pj, cp, _ua in _latest_by_domain(public_crawls).values():
        pages_total += _crawl_page_count(pj, cp)
    return pages_total


def _compute_community_metrics(db: Session) -> dict | None:
    """Compute lifetime community metrics, or None on failure."""
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
        pages_total = _count_public_pages(db, _base)
        return {
            "analyses_total": int(analyses_total),
            "domains_total": int(domains_total),
            "pages_total": int(pages_total),
        }
    except Exception:
        return None


def _build_json_ld(request: Request, site_name: str, abs_page_url: str) -> str | None:
    try:
        json_ld_dict = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": site_name,
            "applicationCategory": "DataExtraction",
            "url": abs_page_url,
            "softwareVersion": (os.getenv("APP_VERSION", "v1") or "v1"),
            "author": {
                "@type": "Organization",
                "name": site_name,
                "url": abs_page_url,
            },
            "dateModified": "2026-08-27",
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
            "offers": {
                "@type": "Offer",
                "name": "Free site analysis",
                "price": "0",
                "priceCurrency": "USD",
                "description": (
                    "Running a site analysis is free. Expert-guided audits "
                    "and remediation roadmaps are priced per engagement."
                ),
            },
            "termsOfService": _abs_url(request, "/terms"),
            "privacyPolicy": _abs_url(request, "/privacy"),
            "isAccessibleForFree": True,
        }
        return json.dumps(json_ld_dict)
    except Exception:
        return None


def _session_params(request: Request) -> tuple[str, str, bool]:
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    session_id = request.cookies.get(cookie_name)
    new_session = False
    if not session_id:
        session_id = str(uuid.uuid4())
        new_session = True
    return cookie_name, session_id, new_session


def _seo_params(site_name: str) -> tuple[str, str]:
    page_title = (
        f"{site_name} — AI Agent Visibility Audit: See How Agents Read Your Site"
    )
    meta_description = (
        "Run a free AI agent visibility audit. MeshWeave checks the website "
        "signals agents can observe and shows what to fix first."
    )
    return page_title, meta_description


def _submitted_banner_params(request: Request) -> tuple[Any, str | None, bool]:
    submitted_id = request.query_params.get("submitted") or None
    submitted_status_url = f"/api/status/{submitted_id}" if submitted_id else None
    submitted_is_private = True if request.query_params.get("private") else False
    return submitted_id, submitted_status_url, submitted_is_private


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    """Homepage with submission form and latest public results.

    Implements:
      - Community metrics banner (lifetime totals) computed inline per request (no cache)
      - Recent analyses: max 9 items, status-ranked (succeeded, running, others), then updated_at DESC
      - Bulk email/page counts, relative time strings, and optional site summary snippet
    """

    rows = _fetch_recent_rows(db)
    email_counts_map, page_counts_map = _build_count_maps(rows)
    domain_run_counts = _count_runs_per_domain(rows)

    # Build item payloads
    items = []
    now = datetime.now(UTC)
    for r in rows:
        items.append(
            _serialize_row(r, page_counts_map, email_counts_map, domain_run_counts, now)
        )

    community_metrics = _compute_community_metrics(db)

    # Ensure session cookie and CSRF token
    _, session_id, new_session = _session_params(request)

    csrf_token = (
        _make_csrf_token(session_id) if _env_bool("WEBAPP_CSRF_ENABLED", False) else ""
    )

    # SEO meta for home (LLM-first): audience + value + CTA so metadata
    # alone answers who/what/why.
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title, meta_description = _seo_params(site_name)
    abs_page_url = _abs_url(request, "/")
    og_image_url = os.getenv("OG_IMAGE_URL") or _abs_url(request, "/static/brain.png")

    # JSON-LD: SoftwareApplication (include once via base template)
    json_ld = _build_json_ld(request, site_name, abs_page_url)

    # Optional banner when a crawl was just started (anonymous redirect target)
    submitted_id, submitted_status_url, submitted_is_private = _submitted_banner_params(
        request
    )

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
        try:
            log_audit("session_created", request=request)
        except Exception:
            pass
    set_csrf_session_cookie(resp, session_id, new_session)
    return resp
