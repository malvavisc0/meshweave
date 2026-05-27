import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl, Product, ScoreSnapshot

# Treat SQLAlchemy declarative models as Any for type checkers to avoid circular/forward-ref analysis issues
Crawl = cast(Any, Crawl)  # pyright: ignore[reportGeneralTypeIssues]
Product = cast(Any, Product)  # pyright: ignore[reportGeneralTypeIssues]
from webapp.services.crawling import run_crawl_task
from webapp.services.site_crawling import run_site_crawl_task
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.config import _env_bool
from webapp.utils.quotas import (
    enforce_concurrent_jobs_limit,
    enforce_daily_site_crawl_limit,
)
from webapp.utils.security import _make_csrf_token, _verify_csrf_token
from webapp.utils.url import _abs_url

router = APIRouter()


def _extract_aax_score(snapshot) -> float | None:
    """Extract AAX composite score from a ScoreSnapshot."""
    if snapshot and snapshot.score_json:
        return snapshot.score_json.get("aax", {}).get("composite")
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


@router.get("/dashboard", response_class=HTMLResponse)
async def my_jobs(
    request: Request,
    page_size: int = 25,
):
    """List current user's jobs with pagination (newest first)."""
    user = await require_auth(request)

    try:
        page_size = int(page_size)
    except Exception:
        page_size = 25
    if page_size not in (25, 50, 100):
        page_size = 25

    with get_session() as s:
        rows_db: list[Any] = (
            s.query(Crawl)
            .filter(Crawl.user_id == user.id)
            .order_by(Crawl.updated_at.desc(), Crawl.id.desc())
            .limit(500)
            .all()
        )

        # Fetch ScoreSnapshots for these crawls
        crawl_ids = [r.id for r in rows_db]
        snapshots = (
            s.query(ScoreSnapshot).filter(ScoreSnapshot.crawl_id.in_(crawl_ids)).all()
        )
        snapshot_map = {ss.crawl_id: ss for ss in snapshots}

    # Build flat items list (for "All Analyses" table) with scores
    items = []
    for r in rows_db[:page_size]:
        ss = snapshot_map.get(r.id)
        aax_score = _extract_aax_score(ss)
        items.append(
            {
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
        )

    # Group by domain for "My Sites" section
    domain_groups: dict[str, list] = {}
    for r in rows_db:
        if r.domain not in domain_groups:
            domain_groups[r.domain] = []
        domain_groups[r.domain].append(r)

    sites = []
    for domain, crawls in domain_groups.items():
        crawls.sort(key=lambda c: c.updated_at or datetime.min, reverse=True)

        latest = crawls[0]
        latest_ss = snapshot_map.get(latest.id)

        # Extract AAX from ScoreSnapshot
        aax_score = _extract_aax_score(latest_ss)
        aax_rating = None
        if latest_ss and latest_ss.score_json:
            aax_data = latest_ss.score_json.get("aax", {})
            aax_rating = aax_data.get("rating") or _get_rating_label(aax_score)

        # Compute trends (compare with previous succeeded crawl)
        aeo_delta = None
        geo_delta = None
        aax_delta = None
        succeeded_crawls = [c for c in crawls if c.status == "succeeded"]
        if len(succeeded_crawls) >= 2:
            prev = succeeded_crawls[1]
            if latest.aeo_score is not None and prev.aeo_score is not None:
                aeo_delta = round(latest.aeo_score - prev.aeo_score, 1)
            if latest.geo_score is not None and prev.geo_score is not None:
                geo_delta = round(latest.geo_score - prev.geo_score, 1)
            prev_ss = snapshot_map.get(prev.id)
            if latest_ss and latest_ss.score_json and prev_ss and prev_ss.score_json:
                prev_aax = prev_ss.score_json.get("aax", {}).get("composite")
                if aax_score is not None and prev_aax is not None:
                    aax_delta = round(float(aax_score) - float(prev_aax), 1)

        # Score history (all succeeded crawls, oldest first)
        history = []
        for c in reversed(succeeded_crawls):
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

        # Top recommendations from latest analysis
        recommendations = []
        if latest_ss and latest_ss.score_json:
            recs = latest_ss.score_json.get("recommendations", [])
            priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
            recs.sort(
                key=lambda r: priority_order.get(r.get("priority", "").lower(), 99)
            )
            for rec in recs[:3]:
                recommendations.append(
                    {
                        "priority": rec.get("priority", "info"),
                        "title": rec.get("title", ""),
                        "impact": rec.get("impact", ""),
                    }
                )

        # Build share URL based on visibility
        share_url = None
        share_disabled = False
        if latest.visibility == "public" and latest.key:
            share_url = f"/analysis/{latest.key}"
        elif getattr(latest, "share_key", None):
            share_url = f"/analysis/share/{latest.share_key}"
        else:
            share_disabled = True

        aeo_rating = latest.aeo_rating or _get_rating_label(latest.aeo_score)
        geo_rating = latest.geo_rating or _get_rating_label(latest.geo_score)

        sites.append(
            {
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
        )

    # Sort sites by most recent analysis
    sites.sort(key=lambda s: s["updated_at"], reverse=True)

    # Compute summary counts for the summary strip
    summary = {
        "domains_tracked": len(sites),
        "improved": sum(
            1 for s in sites if (s["aeo_delta"] or 0) > 0 or (s["geo_delta"] or 0) > 0
        ),
        "declined": sum(
            1 for s in sites if (s["aeo_delta"] or 0) < 0 or (s["geo_delta"] or 0) < 0
        ),
        "need_baseline": sum(1 for s in sites if s["analysis_count"] < 2),
        "running": sum(1 for s in sites if s["latest_status"] == "running"),
        "failed": sum(1 for s in sites if s["latest_status"] == "failed"),
    }

    # Compute attention list (ranked: decline > failed > no baseline)
    attention = sorted(
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

    # Build activity feed from items
    activity = [
        i
        for i in items[:10]
        if i["status"] in ("running", "pending", "failed", "succeeded")
    ][:5]

    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    page_title = f"Dashboard — {site_name}"
    meta_description = "Your recent crawls."

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
    if _env_bool("WEBAPP_CSRF_ENABLED", False):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
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


@router.post("/retry/{crawl_id}")
async def retry_crawl(
    request: Request,
    background_tasks: BackgroundTasks,
    crawl_id: str,
    csrf_token: str | None = Form(None),
):
    """Retry a crawl (owner only) when not running."""
    # CSRF validation
    if _env_bool("WEBAPP_CSRF_ENABLED", False):
        cookie_name = os.getenv("WEBAPP_COOKIE_NAME", "sid")
        session_id = request.cookies.get(cookie_name) or ""
        max_age = int(os.getenv("WEBAPP_CSRF_MAX_AGE", "7200"))
        if not _verify_csrf_token(csrf_token, session_id, max_age_seconds=max_age):
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    # Auth + owner
    user = await require_auth(request)
    row = await require_ownership(request, crawl_id)

    # Enforce cooldown
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    now = datetime.now(UTC)
    if now - row.updated_at < timedelta(minutes=refresh_min_age_minutes):
        raise HTTPException(status_code=429, detail="Retry not available yet")

    if row.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    # Enforce quotas
    enforce_concurrent_jobs_limit(user.id)
    if bool(row.crawl_params):
        enforce_daily_site_crawl_limit(user.id)

    # Reset and schedule
    now = datetime.now(UTC)
    with get_session() as s:
        db_row = s.get(Crawl, crawl_id)
        if not db_row:
            raise HTTPException(status_code=404, detail="Not found")
        db_row.status = "pending"
        db_row.payload_json = None
        db_row.error = None
        try:
            if hasattr(db_row, "error_json"):
                setattr(db_row, "error_json", None)
        except Exception:
            pass
        db_row.updated_at = now

    # Schedule task with force_refresh=True
    if bool(row.crawl_params):
        background_tasks.add_task(run_site_crawl_task, crawl_id, True)
    else:
        background_tasks.add_task(run_crawl_task, crawl_id, True, user_id=user.id)

    return RedirectResponse(
        url=f"/dashboard?notice=retried&job={crawl_id}", status_code=303
    )


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

    # Parse cursor
    cur_ts = None
    cur_id = None
    if cursor:
        try:
            ts_s, pid = str(cursor).split("|", 1)
            from datetime import datetime as _dt

            cur_ts = _dt.fromisoformat(ts_s)
            # If tz-naive, assume UTC
            if not getattr(cur_ts, "tzinfo", None):
                cur_ts = cur_ts.replace(tzinfo=UTC)
            cur_id = pid
        except Exception:
            cur_ts = None
            cur_id = None

    items: list[dict] = []
    next_cursor: str | None = None

    with get_session() as s:
        qry = s.query(Crawl).filter(Crawl.user_id == user.id)

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

        rows = qry.limit(limit + 1).all()

        # Fetch snapshots for score data
        crawl_ids_api = [r.id for r in rows[: limit + 1]]
        snaps_api = (
            s.query(ScoreSnapshot)
            .filter(ScoreSnapshot.crawl_id.in_(crawl_ids_api))
            .all()
        )
        snap_api_map = {ss.crawl_id: ss for ss in snaps_api}

        for r in rows[:limit]:
            ss = snap_api_map.get(r.id)
            aax_score = _extract_aax_score(ss)
            items.append(
                {
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
            )

        if len(rows) > limit:
            last = rows[limit - 1]
            try:
                next_cursor = (
                    f"{(last.updated_at or datetime.now(UTC)).isoformat()}|{last.id}"
                )
            except Exception:
                next_cursor = None

    return {"items": items, "next_cursor": next_cursor}


@router.post("/api/my/jobs/bulk")
async def api_my_jobs_bulk(request: Request, background_tasks: BackgroundTasks):
    """Perform bulk operations on jobs owned by the current user.

    Body:
      { "operation": "retry", "ids": ["..."] }

    Returns:
      { "ok": true, "retried": int }
    """
    user = await require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    operation = (body.get("operation") or "").strip().lower()
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    ids = [str(x) for x in ids if isinstance(x, (str,))]

    if operation not in ("retry",):
        raise HTTPException(status_code=400, detail="unsupported_operation")
    if not ids:
        return {"ok": True, "retried": 0}

    # Load rows owned by user
    with get_session() as s:
        rows = (
            s.query(Crawl)
            .filter(Crawl.user_id == user.id)
            .filter(Crawl.id.in_(ids))
            .all()
        )

    retried = 0
    now = datetime.now(UTC)
    for row in rows:
        # Only retry when not running
        if (row.status or "").lower() == "running":
            continue

        # Enforce quotas per job retry
        try:
            enforce_concurrent_jobs_limit(user.id)
            if bool(row.crawl_params):
                enforce_daily_site_crawl_limit(user.id)
        except HTTPException:
            # Skip this job if quota prevents retry
            continue

        # Reset state and schedule
        with get_session() as s:
            db_row = s.get(Crawl, row.id)
            if not db_row:
                continue
            # Recheck running status in DB to avoid races
            if (db_row.status or "").lower() == "running":
                continue
            db_row.status = "pending"
            db_row.payload_json = None
            db_row.error = None
            try:
                if hasattr(db_row, "error_json"):
                    setattr(db_row, "error_json", None)
            except Exception:
                pass
            db_row.updated_at = now

        # Schedule background task with force_refresh=True
        try:
            if bool(row.crawl_params):
                background_tasks.add_task(run_site_crawl_task, row.id, True)
            else:
                background_tasks.add_task(run_crawl_task, row.id, True, user_id=user.id)
            retried += 1
        except Exception:
            # Skip scheduling failures for individual jobs
            pass

    return {"ok": True, "retried": retried}
