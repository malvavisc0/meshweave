import json
import os
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from webapp.db import get_db
from webapp.infra import templates
from webapp.models import Crawl, CrawlEmail, CrawlLink
from webapp.utils.config import _env_bool
from webapp.utils.logging import log_audit
from webapp.utils.security import _make_csrf_token
from webapp.utils.url import _abs_url

router = APIRouter()


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
            now = datetime.now(timezone.utc)
            base = dt or now
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
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
    rows: List[Crawl] = (
        db.query(Crawl)
        .filter(Crawl.visibility == "public")
        .order_by(
            case(
                (Crawl.status == "succeeded", 0),
                (Crawl.status == "running", 1),
                else_=2,
            ),
            Crawl.updated_at.desc(),
        )
        .limit(9)
        .all()
    )

    ids = [r.id for r in rows] or ["-"]

    # Bulk counts to avoid N+1
    email_counts_map = {}
    page_counts_map = {}

    if ids and len(rows) > 0:
        for cid, cnt in (
            db.query(CrawlEmail.crawl_id, func.count(distinct(CrawlEmail.email)))
            .filter(CrawlEmail.crawl_id.in_(ids))
            .group_by(CrawlEmail.crawl_id)
            .all()
        ):
            email_counts_map[cid] = int(cnt or 0)

        for cid, cnt in (
            db.query(CrawlLink.crawl_id, func.count(distinct(CrawlLink.page_url)))
            .filter(CrawlLink.crawl_id.in_(ids), CrawlLink.type == "internal")
            .group_by(CrawlLink.crawl_id)
            .all()
        ):
            page_counts_map[cid] = int(cnt or 0)

    # Build item payloads
    items = []
    now = datetime.now(timezone.utc)
    for r in rows:
        payload = _safe_json_load(r.payload_json or "")
        title = ""
        description = ""
        og_desc = ""
        try:
            if isinstance(payload, dict):
                pg = payload.get("page") or {}
                title = (pg.get("title") or "").strip()
                description = (pg.get("description") or "").strip()
                og_desc = ((pg.get("og") or {}).get("description") or "").strip()
        except Exception:
            pass

        updated_dt = r.updated_at or now
        try:
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
        except Exception:
            updated_dt = now
        updated_iso = updated_dt.isoformat()
        updated_relative = _relative_time(updated_dt)
        is_new = (now - updated_dt).total_seconds() <= 2 * 3600

        summary_snippet = ""
        if (r.scope or "page") == "site":
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

        items.append(
            {
                "key": r.key,
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "title": title or r.canonical_url or f"{r.domain}{r.path or ''}",
                "scope": r.scope,
                "status": r.status,
                "page_count": page_counts_map.get(r.id, 0),
                "email_count": email_counts_map.get(r.id, 0),
                "updated_iso": updated_iso,
                "updated_relative": updated_relative,
                "is_new": bool(is_new),
                "summary_snippet": (
                    summary_snippet if (r.scope or "page") == "site" else ""
                ),
                # Back-compat fields (legacy templates)
                "updated_at": updated_iso,
            }
        )

    # Community metrics (lifetime totals) computed inline (no caching)
    try:
        analyses_total = (
            db.query(Crawl)
            .filter(Crawl.visibility == "public", Crawl.status == "succeeded")
            .count()
        ) or 0

        emails_total = (
            db.query(func.count(distinct(CrawlEmail.email)))
            .select_from(CrawlEmail)
            .join(Crawl, Crawl.id == CrawlEmail.crawl_id)
            .filter(Crawl.visibility == "public")
            .scalar()
            or 0
        )

        links_external_total = (
            db.query(func.count(CrawlLink.id))
            .join(Crawl, Crawl.id == CrawlLink.crawl_id)
            .filter(Crawl.visibility == "public", CrawlLink.type == "external")
            .scalar()
            or 0
        )

        external_domains_total = (
            db.query(func.count(distinct(CrawlLink.domain)))
            .join(Crawl, Crawl.id == CrawlLink.crawl_id)
            .filter(Crawl.visibility == "public", CrawlLink.type == "external")
            .scalar()
            or 0
        )

        # Sum per-crawl distinct internal pages
        per_crawl_pages = (
            db.query(
                CrawlLink.crawl_id, func.count(distinct(CrawlLink.page_url)).label("cnt")
            )
            .join(Crawl, Crawl.id == CrawlLink.crawl_id)
            .filter(Crawl.visibility == "public", CrawlLink.type == "internal")
            .group_by(CrawlLink.crawl_id)
            .all()
        )
        pages_total = int(sum(int(c or 0) for _, c in per_crawl_pages))
        community_metrics = {
            "analyses_total": int(analyses_total),
            "emails_total": int(emails_total),
            "pages_total": int(pages_total),
            "links_external_total": int(links_external_total),
            "external_domains_total": int(external_domains_total),
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
    site_name = os.getenv("SITE_NAME", "Meshweave")
    page_title = f"{site_name} — Generate Website Insights for Sales & Lead Discovery"
    meta_description = (
        "End-to-end site analysis and content extraction. Turn websites into clean Markdown, "
        "link and email intelligence, and shareable insights with AI-assisted outputs."
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
            "featureList": [
                "Markdown extraction",
                "Link mapping",
                "Email intelligence with sources",
                "AI-assisted summaries",
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
        "home.html",
        {
            "request": request,
            "items": items,
            "community_metrics": community_metrics,
            "csrf_token": csrf_token,
            "login_error": True if request.query_params.get("error") else False,
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
