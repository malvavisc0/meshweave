import json
import os
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
from webapp.utils.progress import progress_view
from webapp.utils.reasons import friendly_reason
from webapp.utils.scoring import (
    PRIORITY_NUMERIC,
    aax_pending,
)
from webapp.utils.scoring import (
    build_score_snapshot_context as _build_score_snapshot_context,
)
from webapp.utils.security import _make_csrf_token
from webapp.utils.times import ensure_utc
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


def _meta_description(desc_from_payload: str) -> str:
    """Build the truncated meta description for the page.

    Derived from the page description only. It never falls back to payload
    markdown: the meta description is embedded in the page head for every
    viewer, and markdown is not part of the public preview allowlist.
    """
    meta_description = (desc_from_payload or "").strip()
    if len(meta_description) > 300:
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
    next_eligible = ensure_utc(updated_at) + timedelta(minutes=min_age_minutes)
    can_proceed = now >= next_eligible
    eta = (
        f"{int((next_eligible - now).total_seconds() / 60)}m" if not can_proceed else ""
    )
    return can_proceed, eta


def _email_source_map(payload: dict | None) -> dict[str, str]:
    """Map each detected email to the page URL it was found on.

    Derived from ``payload.emails.by_url`` (``{url: [emails]}``). Used to
    server-render the owner email table's "Found On" column without any
    client-side lookups.
    """
    mapping: dict[str, str] = {}
    if not payload:
        return mapping
    by_url = (payload.get("emails") or {}).get("by_url") or {}
    for url, addrs in by_url.items():
        addresses = addrs if isinstance(addrs, list) else [addrs]
        for addr in addresses:
            mapping[str(addr)] = (url or "").strip()
    return mapping


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


def _progress_for(row: Crawl, finalize: bool) -> tuple[Crawl, dict | None, bool]:
    """Build SSR progress for in-flight crawls.

    Returns (row, progress_or_None, aax_pending_value). finalize should be
    False for non-owner renders so viewers cannot trigger stale finalization.
    """
    aax = aax_pending(row)
    if str(row.status or "").lower() in ("pending", "running") or aax:
        row, progress = progress_view(row, finalize=finalize)
        return row, progress, aax
    return row, None, aax


def _is_uuid(ref: str) -> bool:
    """Whether the ref parses as a UUID (private analysis id)."""
    try:
        _ = uuid.UUID(ref)
        return True
    except Exception:
        return False


def _analysis_json_ld(
    row: Crawl, payload: dict | None, in_progress: bool, abs_page_url: str, identifier
) -> tuple[str | None, str]:
    """Return (json_ld, reason_stopped_label) for an analysis render."""
    if in_progress:
        return None, ""
    # JSON-LD: CreativeWork (LLM-first)
    counts = _json_ld_count(payload)
    json_ld = _structured_data(
        identifier, row.domain, abs_page_url, row.updated_at, row.status, counts
    )
    return json_ld, _reason_stopped_label(payload)


def _claim_min_hours() -> int:
    """Configured minimum age (hours) before a public analysis can be claimed."""
    try:
        return int(os.getenv("CLAIM_PUBLIC_MIN_AGE_HOURS", "24"))
    except Exception:
        return 24


def _public_refresh_state(row: Crawl, in_progress: bool) -> tuple[bool, str]:
    """Compute (can_refresh, refresh_eta) against the public domain-root cooldown."""
    # Cooldown for refresh (public domain root); irrelevant mid-crawl
    can_refresh = False
    refresh_eta = ""
    if in_progress:
        return can_refresh, refresh_eta
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    now = datetime.now(UTC)
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
    if not public_root:
        return can_refresh, refresh_eta
    next_refresh_eligible = ensure_utc(public_root.updated_at) + timedelta(
        minutes=refresh_min_age_minutes
    )
    can_refresh = now >= next_refresh_eligible
    if not can_refresh:
        refresh_eta = f"{int((next_refresh_eligible - now).total_seconds() / 60)}m"
    return can_refresh, refresh_eta


async def _render_private_view(request: Request, ref: str) -> Response:
    """Render the owner-only private analysis view.

    Only the authenticated owner may see a private analysis. An unauthenticated
    request, an authenticated non-owner, or an ownerless row all return 404 so
    that a private UUID's existence is never revealed.
    """
    try:
        row = await require_ownership(request, ref)
    except HTTPException as exc:
        # 401 (unauthenticated) and 403 (authenticated non-owner) collapse to
        # 404 so that UUID existence is not revealed.
        if exc.status_code in (401, 403):
            raise HTTPException(status_code=404, detail="Not found")
        raise

    row, progress, aax = _progress_for(row, finalize=True)
    in_progress = progress is not None
    payload: dict | None = row.payload_json
    title_from_payload, desc_from_payload = _seo_title_desc(payload)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = _payload_scope(payload, row)
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    page_title = _page_title(
        scope_val, domain_val, path_val, title_from_payload, site_name
    )
    meta_description = _meta_description(desc_from_payload)
    abs_page_url = _abs_url(request, f"/analysis/{row.id}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None
    json_ld, reason_stopped_label = _analysis_json_ld(
        row, payload, in_progress, abs_page_url, str(row.id)
    )
    api_url = f"/api/analysis/private/{row.id}"
    abs_api_url = _abs_url(request, api_url)

    # Cooldown for retry
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    can_retry_cooldown, retry_eta = _cooldown(row.updated_at, refresh_min_age_minutes)
    can_retry = (row.status != "running") and can_retry_cooldown

    # CSRF token for retry form (generate new session if missing and CSRF is enabled)
    csrf_token, cookie_name, session_id, new_session = _csrf(request)
    is_owner, current_user, user_id = _owner_state(request, row)

    _ss_private = None if in_progress else _build_score_snapshot_context(row)
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
            "reason_stopped_label": reason_stopped_label,
            "api_url": api_url,
            "abs_api_url": abs_api_url,
            "can_retry": can_retry,
            "retry_eta": retry_eta,
            "csrf_token": csrf_token,
            # Ownership / gating
            "is_owner": is_owner,
            "user_id": user_id,
            "email_source_map": _email_source_map(payload),
            # SEO/Sharing
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": json_ld,
            # Owner toggles
            "listed": row.listed,
            "can_refresh": can_retry,
            "refresh_eta": retry_eta,
            "score_snapshot": _ss_private,
            "sorted_recommendations": _sorted_recommendations(_ss_private),
            "factor_extremes": _build_factor_extremes(_ss_private),
            "aax_pending": aax,
            "in_progress": in_progress,
            "progress": progress,
        },
    )
    # Prevent indexing of private results
    resp.headers["X-Robots-Tag"] = "noindex"
    _set_session_cookie(resp, new_session, session_id, cookie_name)
    return resp


async def _render_public_view(request: Request, ref: str) -> Response:
    """Render the public analysis view addressed by short key."""
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

    # Public viewers are not owners: never trigger stale finalization
    row, progress, aax = _progress_for(row, finalize=False)
    in_progress = progress is not None
    payload = row.payload_json
    title_from_payload, desc_from_payload = _seo_title_desc(payload)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = _payload_scope(payload, row)
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    page_title = _page_title(
        scope_val, domain_val, path_val, title_from_payload, site_name
    )
    meta_description = _meta_description(desc_from_payload)
    abs_page_url = _abs_url(request, f"/analysis/{row.key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    json_ld, reason_stopped_label = _analysis_json_ld(
        row, payload, in_progress, abs_page_url, str(row.key)
    )

    api_url = f"/api/analysis/public/{row.key}"
    abs_api_url = _abs_url(request, api_url)

    # CSRF token for refresh form (generate new session if missing and CSRF is enabled)
    csrf_token, cookie_name, session_id, new_session = _csrf(request)

    # Claim eligibility inputs for public view (used by client-side countdown/UI)
    claim_min_hours = _claim_min_hours()
    created_at_iso = (row.created_at or datetime.now(UTC)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ownerless = getattr(row, "user_id", None) is None

    can_refresh, refresh_eta = _public_refresh_state(row, in_progress)

    # Ownership/permissions (public view)
    is_owner, current_user, user_id = _owner_state(request, row)

    _ss_public = None if in_progress else _build_score_snapshot_context(row)
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
            "reason_stopped_label": reason_stopped_label,
            "csrf_token": csrf_token,
            # Ownership / gating
            "is_owner": is_owner,
            "user_id": user_id,
            "email_source_map": _email_source_map(payload),
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
            "aax_pending": aax,
            "in_progress": in_progress,
            "progress": progress,
        },
    )
    _set_session_cookie(resp, new_session, session_id, cookie_name)

    # Prevent indexing of non-succeeded public pages
    if row.status != "succeeded":
        resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/analysis/{ref}", response_class=HTMLResponse)
async def view_analysis(request: Request, ref: str):
    """Unified analysis view.

    If 'ref' is a UUID → private analysis (owner-only, claimable if anonymous).
    Else treat 'ref' as public short key.
    """
    # Try UUID → private
    if _is_uuid(ref):
        return await _render_private_view(request, ref)

    # Public by short key
    return await _render_public_view(request, ref)


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
