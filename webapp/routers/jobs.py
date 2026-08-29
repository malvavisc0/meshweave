import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl, ScoreSnapshot
from webapp.services.crawling import run_crawl_task
from webapp.services.site_crawling import run_site_crawl_task
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.quotas import (
    enforce_concurrent_jobs_limit,
    enforce_daily_site_crawl_limit,
)
from webapp.utils.security import _make_csrf_token, verify_request_csrf
from webapp.utils.times import ensure_utc
from webapp.utils.url import _abs_url

router = APIRouter()


def _extract_aax_score(snapshot: ScoreSnapshot | None) -> float | None:
    """Extract AAX composite score from a ScoreSnapshot."""
    if snapshot and snapshot.score_json:
        composite = snapshot.score_json.get("aax", {}).get("composite")
        if isinstance(composite, (int, float)):
            return float(composite)
    return None


def _get_rating_label(score: float | None) -> str | None:
    """Return a human-readable rating label for a score."""
    if score is None:
        return None
    if score >= 80:
        return "Strong"
    if score >= 60:
        return "Moderate"
    return "Weak"


def _serialize_job_row(r, snapshot_map: dict | None = None) -> dict:
    """Serialize a Crawl row into the flat job dict used by tables/APIs."""
    aax_score = _extract_aax_score(snapshot_map.get(r.id) if snapshot_map else None)
    return {
        "id": r.id,
        "scope": "site" if r.crawl_params else "page",
        "domain": r.domain,
        "path": r.path,
        "query": r.query,
        "canonical_url": r.canonical_url,
        "visibility": r.visibility,
        "status": r.status,
        "updated_at": (r.updated_at or datetime.now(UTC)).isoformat(),
        "aeo_score": r.aeo_score,
        "geo_score": r.geo_score,
        "aax_score": aax_score,
        "aeo_rating": r.aeo_rating,
        "geo_rating": r.geo_rating,
    }


def _load_snapshot_map(s, crawl_ids: list) -> dict:
    """Map crawl_id to ScoreSnapshot for the given crawl ids."""
    snapshots = (
        s.query(ScoreSnapshot).filter(ScoreSnapshot.crawl_id.in_(crawl_ids)).all()
    )
    return {ss.crawl_id: ss for ss in snapshots}


def _build_site_card(domain: str, crawls: list, snapshot_map: dict) -> dict:
    """Build the per-domain site card for the dashboard."""
    crawls.sort(key=lambda c: c.updated_at or datetime.min, reverse=True)
    latest = crawls[0]
    latest_ss = snapshot_map.get(latest.id)

    aax_score = _extract_aax_score(latest_ss)
    aax_rating = None
    if latest_ss and latest_ss.score_json:
        aax_data = latest_ss.score_json.get("aax", {})
        aax_rating = aax_data.get("rating") or _get_rating_label(aax_score)

    aeo_delta, geo_delta, aax_delta = _compute_deltas(
        crawls, latest, latest_ss, snapshot_map, aax_score
    )
    history = _build_history(crawls, snapshot_map)
    recommendations = _build_recommendations(latest_ss)
    share_url, share_disabled = _build_share(latest)
    aeo_rating = latest.aeo_rating or _get_rating_label(latest.aeo_score)
    geo_rating = latest.geo_rating or _get_rating_label(latest.geo_score)

    return {
        "domain": domain,
        "latest_id": latest.id,
        "latest_url": latest.canonical_url,
        "latest_status": latest.status,
        "latest_error": latest.error,
        "updated_at": (latest.updated_at or datetime.now(UTC)).isoformat(),
        "analysis_count": len(crawls),
        "aeo_score": latest.aeo_score,
        "geo_score": latest.geo_score,
        "aax_score": aax_score,
        "aeo_rating": aeo_rating,
        "geo_rating": geo_rating,
        "aax_rating": aax_rating,
        "aeo_delta": aeo_delta,
        "geo_delta": geo_delta,
        "aax_delta": aax_delta,
        "history": history,
        "recommendations": recommendations,
        "share_url": share_url,
        "share_disabled": share_disabled,
        "visibility": latest.visibility,
    }


def _score_delta(cur: float | None, prev: float | None) -> float | None:
    """Round the difference between two scores, or None if either is missing."""
    if cur is not None and prev is not None:
        return round(cur - prev, 1)
    return None


def _aax_delta(latest_ss, prev, snapshot_map: dict, aax_score) -> float | None:
    """Compute the AAX composite delta against the previous snapshot."""
    prev_ss = snapshot_map.get(prev.id)
    if latest_ss and latest_ss.score_json and prev_ss and prev_ss.score_json:
        prev_aax = prev_ss.score_json.get("aax", {}).get("composite")
        if aax_score is not None and prev_aax is not None:
            return round(float(aax_score) - float(prev_aax), 1)
    return None


def _compute_deltas(crawls, latest, latest_ss, snapshot_map, aax_score) -> tuple:
    """Compare the latest succeeded crawl against the previous one."""
    aeo_delta = None
    geo_delta = None
    aax_delta = None
    succeeded_crawls = [c for c in crawls if c.status == "succeeded"]
    if len(succeeded_crawls) >= 2:
        prev = succeeded_crawls[1]
        aeo_delta = _score_delta(latest.aeo_score, prev.aeo_score)
        geo_delta = _score_delta(latest.geo_score, prev.geo_score)
        aax_delta = _aax_delta(latest_ss, prev, snapshot_map, aax_score)
    return aeo_delta, geo_delta, aax_delta


def _build_history(crawls, snapshot_map) -> list:
    """Score history of all succeeded crawls, oldest first."""
    history = []
    for c in reversed([cr for cr in crawls if cr.status == "succeeded"]):
        c_aax = None
        c_ss = snapshot_map.get(c.id)
        if c_ss and c_ss.score_json:
            c_aax = c_ss.score_json.get("aax", {}).get("composite")
        history.append(
            {
                "id": c.id,
                "aeo": c.aeo_score,
                "geo": c.geo_score,
                "aax": c_aax,
                "date": (c.updated_at or datetime.now(UTC)).isoformat(),
            }
        )
    return history


def _build_recommendations(latest_ss) -> list:
    """Top recommendations (by priority) from the latest analysis."""
    if not (latest_ss and latest_ss.score_json):
        return []
    recs = latest_ss.score_json.get("recommendations", [])
    priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    recs.sort(key=lambda r: priority_order.get(r.get("priority", "").lower(), 99))
    out = []
    for rec in recs[:3]:
        out.append(
            {
                "priority": rec.get("priority", "info"),
                "title": rec.get("title", ""),
                "impact": rec.get("impact", ""),
            }
        )
    return out


def _build_share(latest) -> tuple[str | None, bool]:
    """Build the public analysis URL and whether it is available for a crawl.

    Private share links have been removed (Phase 2); only the public short-key
    preview URL remains for public analyses.
    """
    if latest.visibility == "public" and latest.key:
        return f"/analysis/{latest.key}", False
    return None, True


def _delta_moved(s: dict, positive: bool) -> bool:
    """Whether a site's aeo/geo delta moved in the given direction."""
    aeo = s["aeo_delta"] or 0
    geo = s["geo_delta"] or 0
    if positive:
        return aeo > 0 or geo > 0
    return aeo < 0 or geo < 0


def _count_delta_sites(sites: list, positive: bool) -> int:
    """Count sites whose aeo/geo delta moved in the given direction."""
    return sum(1 for s in sites if _delta_moved(s, positive))


def _status_count(sites: list, status: str) -> int:
    """Count sites whose latest crawl has the given status."""
    return sum(1 for s in sites if s["latest_status"] == status)


def _summarize_sites(sites: list) -> dict:
    """Compute the dashboard summary strip counts."""
    return {
        "domains_tracked": len(sites),
        "improved": _count_delta_sites(sites, positive=True),
        "declined": _count_delta_sites(sites, positive=False),
        "need_baseline": sum(1 for s in sites if s["analysis_count"] < 2),
        "running": _status_count(sites, "running"),
        "failed": _status_count(sites, "failed"),
    }


def _attention_list(sites: list) -> list:
    """Rank sites needing attention: decline > failed > no baseline."""
    return sorted(
        [
            s
            for s in sites
            if s["latest_status"] == "failed"
            or s["analysis_count"] < 2
            or (s["geo_delta"] is not None and s["geo_delta"] < -5)
        ],
        key=lambda s: (
            s["latest_status"] == "failed",
            (s["geo_delta"] or 0) < -5,
            s["analysis_count"] < 2,
        ),
        reverse=True,
    )[:5]


def _normalize_dashboard_page_size(page_size) -> int:
    """Coerce the dashboard page_size to one of {25, 50, 100}."""
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 25
    return page_size if page_size in (25, 50, 100) else 25


def _load_user_crawls(user_id) -> tuple[list, dict]:
    """Load the user's crawl rows and their score snapshot map."""
    with get_session() as s:
        rows_db: list[Any] = (
            s.query(Crawl)
            .filter(Crawl.user_id == user_id)
            .order_by(Crawl.updated_at.desc(), Crawl.id.desc())
            .limit(500)
            .all()
        )
        crawl_ids = [r.id for r in rows_db]
        snapshot_map = _load_snapshot_map(s, crawl_ids)
    return rows_db, snapshot_map


def _grouped_site_cards(rows_db: list, snapshot_map: dict) -> tuple[list, dict]:
    """Group crawls by domain and build the sorted site card list."""
    # Group by domain for "My Sites" section
    domain_groups: dict[str, list] = {}
    for r in rows_db:
        domain_groups.setdefault(r.domain, []).append(r)

    sites = [
        _build_site_card(domain, crawls, snapshot_map)
        for domain, crawls in domain_groups.items()
    ]

    # Sort sites by most recent analysis
    sites.sort(key=lambda s: s["updated_at"], reverse=True)
    return sites, domain_groups


def _dashboard_activity(items: list) -> list:
    """Build the activity feed from the latest serialized items."""
    return [
        i
        for i in items[:10]
        if i["status"] in ("running", "pending", "failed", "succeeded")
    ][:5]


def _dashboard_csrf(request: Request) -> tuple[str, bool, str | None, str]:
    """Return (csrf_token, new_session, session_id, cookie_name) for the dashboard."""
    # CSRF token for retry forms on this page
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
    return csrf_token, new_session, session_id, cookie_name


@router.get("/dashboard", response_class=HTMLResponse)
async def my_jobs(
    request: Request,
    page_size: int = 25,
):
    """List current user's jobs with pagination (newest first)."""
    user = await require_auth(request)

    page_size = _normalize_dashboard_page_size(page_size)

    rows_db, snapshot_map = _load_user_crawls(user.id)

    # Build flat items list (for "All Analyses" table) with scores
    items = [_serialize_job_row(r, snapshot_map) for r in rows_db[:page_size]]

    sites, domain_groups = _grouped_site_cards(rows_db, snapshot_map)

    # Compute summary counts for the summary strip
    summary = _summarize_sites(sites)

    # Compute attention list (ranked: decline > failed > no baseline)
    attention = _attention_list(sites)

    # Build activity feed from items
    activity = _dashboard_activity(items)

    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = f"Dashboard — {site_name}"
    meta_description = "Your recent crawls."

    csrf_token, new_session, session_id, cookie_name = _dashboard_csrf(request)

    resp = templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "sites": sites,
            "summary": summary,
            "attention": attention,
            "activity": activity,
            "total_analyses": len(rows_db),
            "total_domains": len(domain_groups),
            "has_prev": False,
            "has_next": False,
            "prev_url": None,
            "next_url": None,
            # Notices from query params for user feedback (e.g., after cancel)
            "notice": request.query_params.get("notice") or None,
            "notice_job": request.query_params.get("job") or None,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": _abs_url(request, "/dashboard"),
            "csrf_token": csrf_token,
        },
    )

    # Set session cookie if newly created for CSRF
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
    return resp


@router.get("/cancel")
async def cancel_crawl_no_id(request: Request):
    return RedirectResponse(url="/dashboard?notice=cancel_get", status_code=303)


@router.get("/cancel/{crawl_id}")
async def cancel_crawl_get(request: Request, crawl_id: str):
    # Do not perform cancellation on GET to avoid CSRF; just inform user and redirect
    return RedirectResponse(
        url=f"/dashboard?notice=cancel_get&job={crawl_id}", status_code=303
    )


@router.post("/cancel/{crawl_id}")
async def cancel_crawl(
    request: Request,
    crawl_id: str,
    csrf_token: str | None = Form(None),
):
    """Cancel a running crawl (owner only)."""
    # CSRF validation
    try:
        verify_request_csrf(request, csrf_token)
    except HTTPException:
        return RedirectResponse(
            url=f"/dashboard?notice=csrf_failed&job={crawl_id}", status_code=303
        )

    # Auth + owner
    await require_auth(request)
    try:
        row = await require_ownership(request, crawl_id)
    except HTTPException:
        return RedirectResponse(
            url=f"/dashboard?notice=not_authorized&job={crawl_id}", status_code=303
        )

    if (row.status or "").lower() != "running":
        return RedirectResponse(
            url=f"/dashboard?notice=not_running&job={crawl_id}", status_code=303
        )

    now = datetime.now(UTC)
    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if not db_row:
            return RedirectResponse(
                url=f"/dashboard?notice=not_found&job={crawl_id}", status_code=303
            )
        # Only transition running -> cancelled
        if (db_row.status or "").lower() != "running":
            return RedirectResponse(
                url=f"/dashboard?notice=not_running&job={crawl_id}", status_code=303
            )
        db_row.status = "cancelled"
        db_row.error = "cancelled_by_user"
        db_row.updated_at = now

    return RedirectResponse(
        url=f"/dashboard?notice=cancelled&job={crawl_id}", status_code=303
    )


def _enforce_retry_cooldown(row) -> None:
    """Raise 429 when the retry cooldown has not elapsed for a crawl."""
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    now = datetime.now(UTC)
    if now - ensure_utc(row.updated_at) < timedelta(minutes=refresh_min_age_minutes):
        raise HTTPException(status_code=429, detail="Retry not available yet")


def _reset_job_row(row_id, now: datetime, require_idle: bool) -> bool:
    """Reset a crawl row to pending for retry; False when not resettable.

    When require_idle is True, a row currently marked running in the DB is
    not reset (race-safe recheck used by bulk retries).
    """
    with get_session() as s:
        db_row = s.get(Crawl, row_id)
        if not db_row:
            return False
        if require_idle and (db_row.status or "").lower() == "running":
            return False
        db_row.status = "pending"
        db_row.payload_json = None
        db_row.error = None
        try:
            if hasattr(db_row, "error_json"):
                setattr(db_row, "error_json", None)
        except Exception:
            pass
        db_row.updated_at = now
    return True


def _schedule_retry_task(user, row, background_tasks) -> None:
    """Schedule the retry background task with force_refresh=True."""
    if bool(row.crawl_params):
        background_tasks.add_task(run_site_crawl_task, row.id, True)
    else:
        background_tasks.add_task(run_crawl_task, row.id, True, user_id=user.id)


def _retry_quota_ok(user, row) -> bool:
    """Enforce per-retry quotas; False when a quota blocks the retry."""
    try:
        enforce_concurrent_jobs_limit(user.id)
        if bool(row.crawl_params):
            enforce_daily_site_crawl_limit(user.id)
    except HTTPException:
        # Skip this job if quota prevents retry
        return False
    return True


@router.post("/retry/{crawl_id}")
async def retry_crawl(
    request: Request,
    background_tasks: BackgroundTasks,
    crawl_id: str,
    csrf_token: str | None = Form(None),
):
    """Retry a crawl (owner only) when not running."""
    # CSRF validation
    verify_request_csrf(request, csrf_token)

    # Auth + owner
    user = await require_auth(request)
    row = await require_ownership(request, crawl_id)

    # Enforce cooldown
    _enforce_retry_cooldown(row)

    if row.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    # Enforce quotas
    enforce_concurrent_jobs_limit(user.id)
    if bool(row.crawl_params):
        enforce_daily_site_crawl_limit(user.id)

    # Reset and schedule
    now = datetime.now(UTC)
    if not _reset_job_row(crawl_id, now, require_idle=False):
        raise HTTPException(status_code=404, detail="Not found")
    _schedule_retry_task(user, row, background_tasks)

    return RedirectResponse(
        url=f"/dashboard?notice=retried&job={crawl_id}", status_code=303
    )


def _my_jobs_query(s, user_id, status: str | None, q: str | None, cur_ts, cur_id):
    """Build the filtered, ordered jobs query with the keyset cursor applied."""
    qry = s.query(Crawl).filter(Crawl.user_id == user_id)

    if status:
        qry = qry.filter(func.lower(Crawl.status) == status.strip().lower())

    if q:
        like = f"%{q.strip().lower()}%"
        qry = qry.filter(
            func.lower(Crawl.domain).like(like)
            | func.lower(Crawl.canonical_url).like(like)
        )

    # Sorting: updated_at desc, id desc
    qry = qry.order_by(Crawl.updated_at.desc(), Crawl.id.desc())

    # Keyset cursor
    if cur_ts and cur_id:
        qry = qry.filter(
            (Crawl.updated_at < cur_ts)
            | ((Crawl.updated_at == cur_ts) & (Crawl.id < cur_id))
        )

    return qry


def _jobs_next_cursor(rows: list, limit: int) -> str | None:
    """Build the next-page cursor from the last row on the current page."""
    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    try:
        return f"{(last.updated_at or datetime.now(UTC)).isoformat()}|{last.id}"
    except Exception:
        return None


def _my_jobs_page(s, user, limit: int, qry) -> tuple[list[dict], str | None]:
    """Serialize one page of job rows and build the next-page cursor."""
    rows = qry.limit(limit + 1).all()

    crawl_ids_api = [r.id for r in rows[: limit + 1]]
    snap_api_map = _load_snapshot_map(s, crawl_ids_api)

    items: list[dict] = [_serialize_job_row(r, snap_api_map) for r in rows[:limit]]
    next_cursor = _jobs_next_cursor(rows, limit)
    return items, next_cursor


@router.get("/api/my/jobs")
async def api_my_jobs(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    sort: str = "updated_desc",
    limit: int = 25,
    cursor: str | None = None,
):
    """List current user's jobs with server-side filters and keyset pagination.

    Query params:
      - status: optional status filter (running|succeeded|failed|cancelled|pending)
      - q: optional search token (matches domain or canonical_url)
      - sort: only 'updated_desc' is supported (default)
      - limit: page size (1..100, default 25)
      - cursor: opaque cursor for next page (iso_ts|id)
    """
    user = await require_auth(request)
    try:
        limit = int(limit)
    except Exception:
        limit = 25
    limit = max(1, min(100, limit))
    sort = (sort or "updated_desc").lower()

    cur_ts, cur_id = _parse_jobs_cursor(cursor)

    with get_session() as s:
        qry = _my_jobs_query(s, user.id, status, q, cur_ts, cur_id)
        items, next_cursor = _my_jobs_page(s, user, limit, qry)

    return {"items": items, "next_cursor": next_cursor}


def _parse_jobs_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    """Parse an "iso_ts|id" keyset cursor for jobs pagination."""
    if not cursor:
        return None, None
    try:
        ts_s, pid = str(cursor).split("|", 1)
        from datetime import datetime as _dt

        cur_ts = _dt.fromisoformat(ts_s)
        # If tz-naive, assume UTC
        if not getattr(cur_ts, "tzinfo", None):
            cur_ts = cur_ts.replace(tzinfo=UTC)
        return cur_ts, pid
    except Exception:
        return None, None


async def _bulk_retry_request(request: Request) -> tuple[str, list[str]]:
    """Parse the bulk operation name and id list from the request body."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    operation = (body.get("operation") or "").strip().lower()
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    ids = [str(x) for x in ids if isinstance(x, (str,))]
    return operation, ids


def _owned_crawls_by_ids(user, ids: list[str]) -> list:
    """Load crawl rows owned by the user among the given ids."""
    with get_session() as s:
        return (
            s.query(Crawl)
            .filter(Crawl.user_id == user.id)
            .filter(Crawl.id.in_(ids))
            .all()
        )


@router.post("/api/my/jobs/bulk")
async def api_my_jobs_bulk(request: Request, background_tasks: BackgroundTasks):
    """Perform bulk operations on jobs owned by the current user.

    Body:
      { "operation": "retry", "ids": ["..."] }

    Returns:
      { "ok": true, "retried": int }
    """
    user = await require_auth(request)
    operation, ids = await _bulk_retry_request(request)

    if operation not in ("retry",):
        raise HTTPException(status_code=400, detail="unsupported_operation")
    if not ids:
        return {"ok": True, "retried": 0}

    rows = _owned_crawls_by_ids(user, ids)

    retried = 0
    now = datetime.now(UTC)
    for row in rows:
        if _retry_job(user, row, now, background_tasks):
            retried += 1

    return {"ok": True, "retried": retried}


def _retry_job(user, row, now: datetime, background_tasks) -> bool:
    """Retry a single owned job (respecting quotas/status); True if scheduled."""
    # Only retry when not running
    if (row.status or "").lower() == "running":
        return False

    # Enforce quotas per job retry
    if not _retry_quota_ok(user, row):
        return False

    # Reset state and schedule
    if not _reset_job_row(row.id, now, require_idle=True):
        return False

    # Schedule background task with force_refresh=True
    try:
        _schedule_retry_task(user, row, background_tasks)
    except Exception:
        # Skip scheduling failures for individual jobs
        return False
    return True
