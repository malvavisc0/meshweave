import json
import os
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import joinedload

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.reasons import friendly_reason
from webapp.utils.scoring import (
    PRIORITY_NUMERIC,
)
from webapp.utils.scoring import (
    build_score_snapshot_context as _build_score_snapshot_context,
)
from webapp.utils.security import _make_csrf_token
from webapp.utils.summary import build_summary
from webapp.utils.url import _abs_url

router = APIRouter()


def _sorted_recommendations(ss: dict | None) -> list[dict]:
    """Pre-compute sorted recommendations for the template.

    Sorts by priority (high→low) then by weakest pillar first.
    """
    if not ss or not ss.get("recommendations"):
        return []
    pillar_scores = {
        "aeo": ss.get("aeo_score") or 100,
        "geo": ss.get("geo_score") or 100,
        "aax": ss.get("aax_score") or 100,
    }
    pillar_rank = {
        k: i
        for i, (k, _) in enumerate(sorted(pillar_scores.items(), key=lambda x: x[1]))
    }
    return sorted(
        ss["recommendations"],
        key=lambda r: (
            PRIORITY_NUMERIC.get(r.get("priority", "medium"), 1),
            pillar_rank.get(r.get("pillar", ""), 99),
        ),
    )


def _factor_extremes(factors: dict) -> tuple:
    """Return (weakest_item, strongest_item, first_missing_key).

    Each item is (factor_key, factor_dict) or None.
    first_missing_key is the first factor key where score is None,
    or None if all factors are scored (or dict is empty).
    """
    scored = [(k, v) for k, v in factors.items() if v.get("score") is not None]
    missing = [k for k, v in factors.items() if v.get("score") is None]
    if scored:
        return (
            min(scored, key=lambda x: x[1]["score"]),
            max(scored, key=lambda x: x[1]["score"]),
            missing[0] if missing else None,
        )
    return None, None, missing[0] if missing else None


def _build_factor_extremes(ss: dict | None) -> dict:
    """Build factor_extremes dict for all pillars from score_snapshot."""
    if not ss or not ss.get("score_data"):
        return {
            "aeo": (None, None, None),
            "geo": (None, None, None),
            "aax": (None, None, None),
        }
    sd = ss["score_data"]
    return {
        "aeo": _factor_extremes(sd.get("aeo", {}).get("factors", {})),
        "geo": _factor_extremes(sd.get("geo", {}).get("factors", {})),
        "aax": _factor_extremes(sd.get("aax", {}).get("factors", {})),
    }


def _seo_title_desc(payload: dict | None) -> tuple[str, str]:
    """Extract (title_from_payload, desc_from_payload) from the page payload."""
    title_from_payload = ""
    desc_from_payload = ""
    try:
        if payload:
            pg = payload.get("page") or {}
            title_from_payload = (pg.get("title") or "").strip()
            desc_from_payload = (pg.get("description") or "").strip()
    except Exception:
        pass
    return title_from_payload, desc_from_payload


def _payload_scope(payload: dict | None, row: Crawl) -> str:
    """Compute the effective scope value used for the SEO page title."""
    scope_val = ""
    try:
        if isinstance(payload, dict):
            scope_val = str(payload.get("scope") or "").strip().lower()
    except Exception:
        scope_val = ""
    if not scope_val:
        scope_val = str(getattr(row, "scope", "") or "").strip().lower()
    return scope_val


def _external_count(payload: dict, external_links_count: int) -> int:
    """Apply the extraction reported external count (link count, not page count)."""
    try:
        if payload.get("metrics") and payload["metrics"].get("extraction"):
            ext = payload["metrics"]["extraction"]
            # Do not use internal_count for content_pages_count (it's link count, not page count)
            if ext.get("external_count") is not None:
                external_links_count = int(ext.get("external_count") or 0)
    except Exception:
        pass
    return external_links_count


def _link_counts(payload: dict, internal: int, external: int) -> tuple[int, int]:
    """Count internal/external link lists, keeping the larger external count."""
    try:
        if payload.get("links"):
            if isinstance(payload["links"].get("internal"), list):
                internal = len(payload["links"]["internal"])
            if isinstance(payload["links"].get("external"), list):
                external = max(external, len(payload["links"]["external"]))
    except Exception:
        pass
    return internal, external


def _emails_count(payload: dict, emails_count: int) -> int:
    """Set the email count from the payload counts when present."""
    try:
        if payload.get("emails") and payload["emails"].get("counts"):
            emails_count = int(payload["emails"]["counts"].get("total_unique") or 0)
    except Exception:
        pass
    return emails_count


def _content_pages_count(payload: dict, content_pages_count: int) -> int:
    """Count content pages from pages list or summary visited_count."""
    try:
        if isinstance(payload.get("pages"), list):
            content_pages_count = len(payload["pages"])
        elif (
            isinstance(payload.get("summary"), dict)
            and payload["summary"].get("visited_count") is not None
        ):
            content_pages_count = int(payload["summary"]["visited_count"] or 0)
    except Exception:
        pass
    return content_pages_count


def _json_ld_count(payload: dict | None) -> dict:
    """Derive content/email/link counts from the payload for JSON-LD."""
    if not payload:
        return {
            "content_pages_count": 0,
            "emails_count": 0,
            "internal_links_count": 0,
            "external_links_count": 0,
        }
    external_links_count = _external_count(payload, 0)
    internal_links_count, external_links_count = _link_counts(
        payload, 0, external_links_count
    )
    emails_count = _emails_count(payload, 0)
    content_pages_count = _content_pages_count(payload, 0)
    return {
        "content_pages_count": content_pages_count,
        "emails_count": emails_count,
        "internal_links_count": internal_links_count,
        "external_links_count": external_links_count,
    }


def _page_title(
    scope_val: str,
    domain_val: str,
    path_val: str,
    title_from_payload: str,
    site_name: str,
) -> str:
    """Build the SEO-friendly page title for the given scope."""
    if scope_val == "site":
        return f"{domain_val} Site Analysis — Pages, Links, Emails | {site_name}"
    path_or_title = title_from_payload or path_val
    return f"Page Analysis — {path_or_title} — {domain_val} | {site_name}"


def _meta_description(desc_from_payload: str, payload: dict | None) -> str:
    """Build the truncated meta description for the page."""
    meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
        "markdown", ""
    )
    if meta_description and len(meta_description) > 300:
        meta_description = meta_description[:297] + "..."
    return meta_description


def _structured_data(
    identifier: str,
    domain: str,
    abs_page_url: str,
    updated_at: datetime | None,
    status: str | None,
    counts: dict,
) -> str:
    """Build the JSON-LD CreativeWork payload (with fallback)."""
    try:
        return json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CreativeWork",
                "name": "MeshWeave Analysis",
                "identifier": identifier,
                "about": (domain or "").strip(),
                "url": abs_page_url,
                "dateModified": (updated_at or datetime.now(UTC)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "creativeWorkStatus": (status or "").title(),
                "measurementTechnique": [
                    "web-crawl",
                    "markdown-extraction",
                    "link-analysis",
                    "email-detection",
                ],
                "isAccessibleForFree": True,
                "keywords": [
                    "markdown",
                    "link map",
                    "email intelligence",
                    "ai summary",
                ],
                "additionalProperty": [
                    {
                        "@type": "PropertyValue",
                        "name": "content_pages_count",
                        "value": str(counts["content_pages_count"]),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "emails_count",
                        "value": str(counts["emails_count"]),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "internal_links_count",
                        "value": str(counts["internal_links_count"]),
                    },
                    {
                        "@type": "PropertyValue",
                        "name": "external_links_count",
                        "value": str(counts["external_links_count"]),
                    },
                ],
            }
        )
    except Exception:
        return json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "CreativeWork",
                "name": "MeshWeave Analysis",
                "identifier": identifier,
                "about": (domain or "").strip(),
                "url": abs_page_url,
            }
        )


def _reason_stopped_label(payload: dict | None) -> str:
    """Build the friendly reason-stopped label for the template."""
    return (
        friendly_reason(payload.get("summary", {}).get("reason_stopped", ""))
        if payload and payload.get("summary")
        else ""
    )


def _cooldown(updated_at: datetime, min_age_minutes: int) -> tuple[bool, str]:
    """Return (can_proceed, eta_label) based on the row's update cooldown."""
    now = datetime.now(UTC)
    next_eligible = updated_at + timedelta(minutes=min_age_minutes)
    can_proceed = now >= next_eligible
    eta = (
        f"{int((next_eligible - now).total_seconds() / 60)}m" if not can_proceed else ""
    )
    return can_proceed, eta


def _owner_state(request: Request, row: Crawl) -> tuple:
    """Return (is_owner, current_user, user_id_string)."""
    current_user = getattr(request.state, "current_user", None)
    is_owner = bool(current_user and getattr(row, "user_id", None) == current_user.id)
    return is_owner, current_user, (current_user.id if current_user else "")


def _csrf(request: Request) -> tuple:
    """Return (csrf_token, cookie_name, session_id, new_session).

    Generates a fresh session id when CSRF is enabled and none is present.
    """
    cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
    session_id = request.cookies.get(cookie_name)
    new_session = False
    if _env_bool("WEBAPP_CSRF_ENABLED", False) and not session_id:
        session_id = str(uuid.uuid4())
        new_session = True
    csrf_token = (
        _make_csrf_token(session_id)
        if (_env_bool("WEBAPP_CSRF_ENABLED", False) and session_id)
        else ""
    )
    return csrf_token, cookie_name, session_id, new_session


def _set_session_cookie(
    resp: Response, new_session: bool, session_id: str | None, cookie_name: str
) -> None:
    """Persist the generated session cookie when a new one was created."""
    if new_session and session_id:
        cookie_secure = _env_bool("WEBAPP_COOKIE_SECURE", False)
        session_ttl = int(os.getenv("WEBAPP_SESSION_TTL", "43200"))
        resp.set_cookie(
            key=cookie_name,
            value=str(session_id),
            max_age=session_ttl,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )


# Simple in-memory rate limiter for share toggle (max 5 per hour per user)
share_toggle_limits: dict[str, list[float]] = {}


@router.get("/analysis/shared/{share_key}", response_class=HTMLResponse)
async def view_shared_analysis(request: Request, share_key: str):
    """View private analysis via shareable link."""
    with get_session() as s:
        row = (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .filter(Crawl.share_key == share_key, Crawl.visibility == "private")
            .first()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    payload: dict | None = row.payload_json

    # Similar to public view, but with Unlisted badge and no claim
    # Compute SEO/meta
    title_from_payload = ""
    desc_from_payload = ""
    try:
        if payload:
            pg = payload.get("page") or {}
            title_from_payload = (pg.get("title") or "").strip()
            desc_from_payload = (pg.get("description") or "").strip()
    except Exception:
        pass

    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = str(getattr(row, "scope", "") or "").strip().lower()
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    if scope_val == "site":
        page_title = f"{domain_val} Site Analysis — Pages, Links, Emails | {site_name}"
    else:
        path_or_title = title_from_payload or path_val
        page_title = f"Page Analysis — {path_or_title} — {domain_val} | {site_name}"

    meta_description = (desc_from_payload or "").strip() or (payload or {}).get(
        "markdown", ""
    )
    if meta_description and len(meta_description) > 300:
        meta_description = meta_description[:297] + "..."

    abs_page_url = _abs_url(request, f"/analysis/shared/{share_key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    summary = build_summary(row, payload)

    _ss_shared = _build_score_snapshot_context(row)
    # Do not show the analysis page until AAX is calculated
    if row.status == "succeeded" and _ss_shared and _ss_shared.get("aax_score") is None:
        row.status = "running"

    resp = templates.TemplateResponse(
        request,
        "result.html",
        {
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "canonical_url": row.canonical_url,
            "visibility": "unlisted",  # Special badge
            "listed": False,
            "key": None,  # No key for shared
            "status": row.status,
            "error": row.error,
            "payload": payload,
            "api_url": f"/api/analysis/private/{row.id}",
            "abs_api_url": _abs_url(request, f"/api/analysis/private/{row.id}"),
            "summary": summary,
            "reason_stopped_label": (
                friendly_reason(payload.get("summary", {}).get("reason_stopped", ""))
                if payload and payload.get("summary")
                else ""
            ),
            "csrf_token": "",
            "is_owner": False,  # Shared viewers are not owners
            "user_id": "",
            "id": row.id,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": None,
            "created_at": "",
            "claim_min_hours": 24,
            "ownerless": False,
            "can_retry": False,
            "retry_eta": "",
            "can_refresh": False,
            "refresh_eta": "",
            "score_snapshot": _ss_shared,
            "sorted_recommendations": _sorted_recommendations(_ss_shared),
            "factor_extremes": _build_factor_extremes(_ss_shared),
        },
    )
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/analysis/{ref}", response_class=HTMLResponse)
async def view_analysis(request: Request, ref: str):
    """Unified analysis view.

    If 'ref' is a UUID → private analysis (owner-only, claimable if anonymous).
    Else treat 'ref' as public short key.
    """
    # Try UUID → private
    is_uuid = False
    try:
        _ = uuid.UUID(ref)
        is_uuid = True
    except Exception:
        is_uuid = False

    if is_uuid:
        # Private (owner-only)
        # If this private job was created anonymously (no owner), allow the first authenticated
        # user reaching this page to claim ownership. Otherwise, enforce ownership.
        with get_session() as s:
            db_row = s.get(Crawl, ref, options=[joinedload(Crawl.score_snapshot)])
            if not db_row:
                raise HTTPException(status_code=404, detail="Not found")
            if not getattr(db_row, "user_id", None):
                user = await require_auth(request)
                db_row.user_id = user.id
                s.flush()
                row = db_row
            else:
                row = await require_ownership(request, ref)

        payload: dict | None = row.payload_json
        title_from_payload, desc_from_payload = _seo_title_desc(payload)
        site_name = os.getenv("SITE_NAME", "MeshWeave")
        scope_val = _payload_scope(payload, row)
        domain_val = (row.domain or "").strip()
        path_val = (row.path or "").strip() or "/"
        page_title = _page_title(
            scope_val, domain_val, path_val, title_from_payload, site_name
        )
        meta_description = _meta_description(desc_from_payload, payload)
        abs_page_url = _abs_url(request, f"/analysis/{row.id}")
        og_image_url = os.getenv("OG_IMAGE_URL") or None
        counts = _json_ld_count(payload)
        # JSON-LD: CreativeWork (LLM-first)
        json_ld = _structured_data(
            str(row.id), row.domain, abs_page_url, row.updated_at, row.status, counts
        )
        summary = build_summary(row, payload)
        reason_stopped_label = _reason_stopped_label(payload)
        api_url = f"/api/analysis/private/{row.id}"
        abs_api_url = _abs_url(request, api_url)

        # Cooldown for retry
        refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
        can_retry_cooldown, retry_eta = _cooldown(
            row.updated_at, refresh_min_age_minutes
        )
        can_retry = (row.status != "running") and can_retry_cooldown

        # CSRF token for retry form (generate new session if missing and CSRF is enabled)
        csrf_token, cookie_name, session_id, new_session = _csrf(request)
        is_owner, current_user, user_id = _owner_state(request, row)

        _ss_private = _build_score_snapshot_context(row)
        # Do not show the analysis page until AAX is calculated
        if (
            row.status == "succeeded"
            and _ss_private
            and _ss_private.get("aax_score") is None
        ):
            row.status = "running"
        resp = templates.TemplateResponse(
            request,
            "result.html",
            {
                "id": row.id,
                "domain": row.domain,
                "path": row.path,
                "query": row.query,
                "canonical_url": row.canonical_url,
                "visibility": row.visibility,
                "status": row.status,
                "error": row.error,
                "payload": payload,
                "summary": summary,
                "reason_stopped_label": reason_stopped_label,
                "api_url": api_url,
                "abs_api_url": abs_api_url,
                "can_retry": can_retry,
                "retry_eta": retry_eta,
                "csrf_token": csrf_token,
                # Ownership / gating
                "is_owner": is_owner,
                "user_id": user_id,
                "can_view_leads": is_owner,
                # SEO/Sharing
                "page_title": page_title,
                "meta_description": meta_description,
                "abs_page_url": abs_page_url,
                "og_image_url": og_image_url,
                "site_name": site_name,
                "json_ld": json_ld,
                # Owner toggles
                "listed": row.listed,
                "share_url": (
                    f"/analysis/shared/{row.share_key}" if row.share_key else ""
                ),
                "can_refresh": can_retry,
                "refresh_eta": retry_eta,
                "score_snapshot": _ss_private,
                "sorted_recommendations": _sorted_recommendations(_ss_private),
                "factor_extremes": _build_factor_extremes(_ss_private),
            },
        )
        # Prevent indexing of private results
        resp.headers["X-Robots-Tag"] = "noindex"
        _set_session_cookie(resp, new_session, session_id, cookie_name)
        return resp

    # Public by short key
    with get_session() as s:
        row_result = (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .filter(Crawl.key == ref, Crawl.visibility == "public")
            .one_or_none()
        )
    if not row_result:
        raise HTTPException(status_code=404, detail="Not found")
    row = row_result

    payload = row.payload_json
    title_from_payload, desc_from_payload = _seo_title_desc(payload)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = _payload_scope(payload, row)
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    page_title = _page_title(
        scope_val, domain_val, path_val, title_from_payload, site_name
    )
    meta_description = _meta_description(desc_from_payload, payload)
    abs_page_url = _abs_url(request, f"/analysis/{row.key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: CreativeWork (LLM-first)
    counts = _json_ld_count(payload)
    json_ld = _structured_data(
        str(row.key), row.domain, abs_page_url, row.updated_at, row.status, counts
    )
    summary = build_summary(row, payload)
    reason_stopped_label = _reason_stopped_label(payload)

    # CSV/summary endpoints
    api_summary_url = f"/api/analysis/public/{row.key}/summary"
    emails_csv_url = f"/api/analysis/public/{row.key}/emails.csv"
    links_csv_url = f"/api/analysis/public/{row.key}/links.csv"
    top_domains_csv_url = f"/api/analysis/public/{row.key}/top-external-domains.csv"
    api_url = f"/api/analysis/public/{row.key}"
    abs_api_url = _abs_url(request, api_url)

    # CSRF token for refresh form (generate new session if missing and CSRF is enabled)
    csrf_token, cookie_name, session_id, new_session = _csrf(request)

    # Claim eligibility inputs for public view (used by client-side countdown/UI)
    try:
        claim_min_hours = int(os.getenv("CLAIM_PUBLIC_MIN_AGE_HOURS", "24"))
    except Exception:
        claim_min_hours = 24
    created_at_iso = (row.created_at or datetime.now(UTC)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ownerless = getattr(row, "user_id", None) is None

    # Cooldown for refresh (public domain root)
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    now = datetime.now(UTC)
    can_refresh = False
    refresh_eta = ""
    with get_session() as s:
        public_root = (
            s.query(Crawl)
            .filter(
                Crawl.domain == row.domain,
                Crawl.visibility == "public",
                Crawl.path == "/",
                Crawl.query == "",
            )
            .first()
        )
        if public_root:
            next_refresh_eligible = public_root.updated_at + timedelta(
                minutes=refresh_min_age_minutes
            )
            can_refresh = now >= next_refresh_eligible
            if not can_refresh:
                refresh_eta = (
                    f"{int((next_refresh_eligible - now).total_seconds() / 60)}m"
                )

    # Ownership/permissions (public view)
    is_owner, current_user, user_id = _owner_state(request, row)

    _ss_public = _build_score_snapshot_context(row)
    # Do not show the analysis page until AAX is calculated
    if row.status == "succeeded" and _ss_public and _ss_public.get("aax_score") is None:
        row.status = "running"
    resp = templates.TemplateResponse(
        request,
        "result.html",
        {
            "domain": row.domain,
            "path": row.path,
            "query": row.query,
            "canonical_url": row.canonical_url,
            "visibility": row.visibility,
            "listed": row.listed,
            "key": row.key,
            "status": row.status,
            "error": row.error,
            "payload": payload,
            "api_url": api_url,
            "abs_api_url": abs_api_url,
            # Enriched
            "summary": summary,
            "reason_stopped_label": reason_stopped_label,
            "api_summary_url": api_summary_url,
            "emails_csv_url": emails_csv_url,
            "links_csv_url": links_csv_url,
            "top_domains_csv_url": top_domains_csv_url,
            "csrf_token": csrf_token,
            # Ownership / gating
            "is_owner": is_owner,
            "user_id": user_id,
            # Provide private id to owners for chat scoping
            "id": row.id,
            # SEO/Sharing
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": json_ld,
            # Claim eligibility (public ownerless)
            "created_at": created_at_iso,
            "claim_min_hours": claim_min_hours,
            "ownerless": ownerless,
            # Refresh cooldown
            "can_refresh": can_refresh,
            "refresh_eta": refresh_eta,
            "score_snapshot": _ss_public,
            "sorted_recommendations": _sorted_recommendations(_ss_public),
            "factor_extremes": _build_factor_extremes(_ss_public),
        },
    )
    _set_session_cookie(resp, new_session, session_id, cookie_name)

    # Prevent indexing of non-succeeded public pages
    if row.status != "succeeded":
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.post("/analysis/{crawl_id}/set-listed")
async def set_listed(request: Request, crawl_id: str):
    """Set listed status for a public analysis (owner only)."""
    await require_auth(request)
    row = await require_ownership(request, crawl_id)
    if row.visibility != "public":
        raise HTTPException(
            status_code=400, detail="Only public analyses can be listed/unlisted"
        )

    try:
        data = await request.json()
        listed = bool(data.get("listed", True))
    except Exception:
        listed = True

    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if db_row:
            db_row.listed = listed
            s.commit()

    return {"ok": True, "listed": listed}


@router.post("/analysis/{crawl_id}/set-share")
async def set_share(request: Request, crawl_id: str):
    """Enable/disable shareable link for a private analysis (owner only)."""
    user = await require_auth(request)
    row = await require_ownership(request, crawl_id)
    if row.visibility != "private":
        raise HTTPException(
            status_code=400, detail="Only private analyses can have share links"
        )

    # Rate limiting: max 5 toggles per hour per user
    now = time.time()
    user_key = f"share_toggle_{user.id}"
    if user_key not in share_toggle_limits:
        share_toggle_limits[user_key] = []
    share_toggle_limits[user_key] = [
        t for t in share_toggle_limits[user_key] if now - t < 3600
    ]
    if len(share_toggle_limits[user_key]) >= 5:
        raise HTTPException(
            status_code=429, detail="Too many share toggles. Try again later."
        )
    share_toggle_limits[user_key].append(now)

    try:
        data = await request.json()
        enabled = bool(data.get("enabled", False))
    except Exception:
        enabled = False

    share_key = None
    if enabled:
        share_key = secrets.token_urlsafe(16)  # 32 chars
        # Ensure unique
        with get_session() as s:
            while s.query(Crawl).filter(Crawl.share_key == share_key).first():
                share_key = secrets.token_urlsafe(16)

    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if db_row:
            db_row.share_key = share_key
            s.commit()

    return {"ok": True, "enabled": enabled, "share_key": share_key}
