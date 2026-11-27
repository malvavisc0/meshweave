"""Versioned JSON API (v1) — the surface the funnel's gates point at.

Bearer-key authenticated only (no browser sessions; session-based access to
these resources is a separate, deliberately deferred decision). All
resources are owner-scoped: a key only ever sees its owner's crawls.

Endpoints:
- POST /api/v1/analyses — bulk submit (page or site), durable queue
- GET  /api/v1/analyses — the caller's crawl list, cursor-paginated
- GET  /api/v1/analyses/{crawl_id} — site-side contract (202 while in progress)
- POST /api/v1/analyses/{crawl_id}/visual — CTA visual assessment (?images=1)
- GET  /api/v1/analyses/{crawl_id}/report.md — unbranded client-ready report
- GET  /api/v1/analyses/{crawl_id}/diff[.md] — proof-of-work diff

Report and diff exports live in ``webapp.routers.api_v1_reports``; the
visual assessment lives in ``webapp.routers.api_v1_visual``. Both are
included below so the v1 route table stays one mount.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func

from meshweave.scoring.interpretation import interpret_profile
from webapp.db import get_session
from webapp.models import Crawl
from webapp.routers.api import _bearer_user_id
from webapp.routers.api_v1_reports import router as _reports_router
from webapp.routers.api_v1_visual import router as _visual_router
from webapp.routers.submissions import (
    _find_latest_crawl,
    _normalize_domain_field,
    _now_refreshing,
    _require_page_url,
)
from webapp.utils.body_size import enforce_body_size
from webapp.utils.export import answerability_questions
from webapp.utils.quotas import (
    _concurrent_jobs_limit,
    _count_user_concurrent_jobs,
    _count_user_daily_site_crawls,
    _daily_site_limit,
)
from webapp.utils.scoring import lens_label
from webapp.utils.url import canonicalize_url, reject_internal_target

router = APIRouter()
router.include_router(_reports_router)
router.include_router(_visual_router)

BULK_BATCH_CAP = 25


# ── helpers ─────────────────────────────────────────────────────────


def _require_bearer_user(request: Request) -> str:
    """Resolve the caller's user id from a Bearer key or raise 401.

    Counts the call against the key's daily usage row (best-effort) so the
    deferred rate-limit decision has real p50/p95 data to work with.
    """
    user_id = _bearer_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="API key required")
    _enforce_key_rate_limit(request)
    try:
        from webapp.services.api_usage import record_usage

        record_usage(getattr(request.state, "bearer_api_key_id", None), calls=1)
    except Exception:
        pass
    return user_id


def _stamp_first_use(request: Request) -> None:
    """Stamp the key's first use once the call is known-successful.

    Reaching the call site means the endpoint is returning a 2xx — every
    failure path raises — so this is the "the key actually works for
    them" activation signal.
    """
    try:
        from webapp.routers.api import stamp_key_first_use

        stamp_key_first_use(request)
    except Exception:
        pass


def _enforce_key_rate_limit(request: Request) -> None:
    """Per-key API rate limit: calls per window per Bearer key.

    Counts come from the key's own daily usage rows (already recorded on
    every call), so no new store is needed. Fail-open on accounting
    errors — the limit is abuse control, not a data gate.
    """
    key_id = getattr(getattr(request, "state", None), "bearer_api_key_id", None)
    if not key_id:
        return
    limit = _int_env("WEBAPP_API_RATE_LIMIT_MAX", 240)
    window_sec = _int_env("WEBAPP_API_RATE_LIMIT_WINDOW_SEC", 3600)
    if limit <= 0:
        return
    try:
        from datetime import UTC as _UTC

        cutoff = datetime.now(_UTC) - timedelta(seconds=window_sec)
        with get_session() as s:
            from webapp.models import ApiKeyUsageDaily

            used = (
                s.query(func.sum(ApiKeyUsageDaily.calls))
                .filter(
                    ApiKeyUsageDaily.api_key_id == key_id,
                    ApiKeyUsageDaily.updated_at >= cutoff,
                )
                .scalar()
                or 0
            )
        if int(used) >= limit:
            raise HTTPException(status_code=429, detail="API rate limit exceeded")
    except HTTPException:
        raise
    except Exception:
        pass


def _int_env(name: str, default: int) -> int:
    """Read an integer env var with a fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _owned_crawl(user_id: str, crawl_id: str) -> Crawl:
    """Load a crawl owned by the caller; 404 otherwise (never 403).

    The score snapshot is eager-loaded so the returned (detached) row can
    be used by the diff/export builders without lazy-load trips.
    """
    from sqlalchemy.orm import joinedload

    with get_session() as s:
        row = (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .filter(Crawl.id == crawl_id)
            .one_or_none()
        )
        if not row or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Not found")
        s.expunge(row)
        return row


def _crawl_summary(row: Crawl) -> dict[str, Any]:
    return {
        "id": row.id,
        "url": row.url,
        "domain": row.domain,
        "scope": "site" if row.crawl_params is not None else "page",
        "status": row.status,
        "aeo_score": row.aeo_score,
        "geo_score": row.geo_score,
        "aeo_rating": row.aeo_rating,
        "geo_rating": row.geo_rating,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _contract_factors(score_json: dict, lens: str) -> dict[str, dict]:
    """Current-mix factor map for one lens: score and weight per factor."""
    factors = (score_json.get(lens) or {}).get("factors") or {}
    return {
        key: {"score": factor.get("score"), "weight": factor.get("weight")}
        for key, factor in factors.items()
        if isinstance(factor, dict)
    }


def _contract_lens_scores(row: Crawl, score_json: dict) -> dict[str, dict]:
    """Per-lens score, rating, and factors for the site-side contract."""
    aax_composite = (score_json.get("aax") or {}).get("composite")
    scores = {"aeo": row.aeo_score, "geo": row.geo_score, "aax": aax_composite}
    return {
        lens: {
            "score": scores[lens],
            "rating": (score_json.get(lens) or {}).get("rating"),
            "factors": _contract_factors(score_json, lens),
        }
        for lens in ("aeo", "geo", "aax")
    }


def _contract_answerability(score_json: dict) -> dict[str, Any]:
    """Answerability evidence: metrics plus per-question grounded answers."""
    factors = (score_json.get("aeo") or {}).get("factors") or {}
    raw = (factors.get("answerability") or {}).get("raw") or {}
    return {
        "answer_support": raw.get("answer_support") or 0.0,
        "evidence_coverage": raw.get("evidence_coverage") or 0.0,
        "question_count": raw.get("question_count") or 0,
        "questions": answerability_questions(score_json),
    }


def _contract_interpretation(row: Crawl, score_json: dict) -> dict[str, Any]:
    """Interpretation text for the contract: lens names under public labels."""
    interp = interpret_profile(
        row.aeo_score,
        row.geo_score,
        (score_json.get("aax") or {}).get("composite"),
    )
    for key in ("weakest_lens", "strongest_lens"):
        interp[key] = lens_label(interp.get(key))
    return interp


def _analysis_contract(row: Crawl) -> dict[str, Any]:
    """The site-side contract for one succeeded analysis.

    Factors with weights, lens scores and ratings, interpretation text,
    and answerability evidence — and nothing else: no crawl payload
    internals and no legacy simulation or manual-input fields.
    """
    snap = getattr(row, "score_snapshot", None)
    score_json = (snap.score_json if snap is not None else None) or {}
    return {
        "id": row.id,
        "url": row.url,
        "domain": row.domain,
        "path": row.path,
        "query": row.query,
        "scope": "site" if row.crawl_params is not None else "page",
        "status": row.status,
        "scoring_version": (
            getattr(snap, "scoring_version", None) or row.scoring_version
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "scores": _contract_lens_scores(row, score_json),
        "interpretation": _contract_interpretation(row, score_json),
        "answerability": _contract_answerability(score_json),
    }


# ── D1: bulk submit + list + fetch ──────────────────────────────────


def _parse_bulk_body(data: Any) -> tuple[list, str]:
    """Validate the bulk-submit body; raises 400 on shape errors.

    Also rejects non-dict JSON bodies (lists, strings, numbers) with 400.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    urls = _parse_bulk_urls(data.get("urls"))
    scope = _parse_bulk_scope(data.get("scope"))
    return urls, scope


def _parse_bulk_urls(raw: Any) -> list:
    """Validate the urls field: a non-empty list of non-empty strings."""
    if not isinstance(raw, list) or not raw:
        raise HTTPException(
            status_code=400, detail="urls must be a non-empty list of strings"
        )
    if len(raw) > BULK_BATCH_CAP:
        raise HTTPException(
            status_code=400, detail=f"batch too large (max {BULK_BATCH_CAP} urls)"
        )
    urls = [str(u or "").strip() for u in raw if str(u or "").strip()]
    if not urls:
        raise HTTPException(
            status_code=400, detail="urls must be a non-empty list of strings"
        )
    return urls


def _parse_bulk_scope(raw: Any) -> str:
    """Validate the scope field: 'page' or 'site' (default page)."""
    scope = str(raw or "page").strip().lower()
    if scope not in ("page", "site"):
        raise HTTPException(status_code=400, detail="scope must be 'page' or 'site'")
    return scope


def _site_limits() -> dict:
    """Server-side defaults for bulk site crawls (scope-aware, truthy)."""
    try:
        max_pages = int(os.getenv("AUTH_SITE_MAX_PAGES_DEFAULT", "200"))
    except Exception:
        max_pages = 200
    try:
        budget_ms = int(os.getenv("AUTH_SITE_TIME_BUDGET_MS_DEFAULT", "600000"))
    except Exception:
        budget_ms = 600000
    return {"max_pages": max_pages, "time_budget_ms": budget_ms}


def _normalize_target(uval: str, scope: str) -> tuple[str, str, str, str]:
    """Canonicalize one URL for the batch; raises HTTPException when invalid."""
    _require_page_url(uval)
    dom, path, query, canon_url = canonicalize_url(uval)
    if reject_internal_target(dom):
        raise HTTPException(
            status_code=400,
            detail="URL targets a non-public network address",
        )
    if scope == "site":
        dom = _normalize_domain_field(dom)
        path, query = "/", ""
    return dom, path, query, canon_url


class _BatchBudget:
    """Batch-atomic quota accounting: slots consumed by this batch count as
    we admit, so a 25-url batch can't sneak past a limit one row at a time."""

    def __init__(self, user_id: str) -> None:
        self.concurrent_used = _count_user_concurrent_jobs(user_id)
        self.concurrent_limit = _concurrent_jobs_limit()
        self.site_daily_used = _count_user_daily_site_crawls(user_id)
        self.site_daily_limit = _daily_site_limit()
        self.admitted = 0

    def allows(self, scope: str) -> bool:
        if self.concurrent_used + self.admitted >= self.concurrent_limit:
            return False
        if (
            scope == "site"
            and self.site_daily_used + self.admitted >= self.site_daily_limit
        ):
            return False
        return True


def _admit_one(
    s, user_id: str, raw: Any, scope: str, budget: _BatchBudget, now
) -> dict:
    """Admit or reject one URL; returns its outcome entry."""
    entry: dict[str, Any] = {"url": str(raw or "").strip(), "crawl_id": None}
    try:
        dom, path, query, canon_url = _normalize_target(entry["url"], scope)
    except HTTPException:
        entry["status"] = "invalid"
        return entry
    if not budget.allows(scope):
        # Quota must be checked BEFORE any history is touched: retiring the
        # previous latest here would destroy the revision and strand its
        # queued job on a rejected re-submit.
        entry["status"] = "quota"
        return entry
    existing = _find_latest_crawl(
        s, "private", dom, path, query, user_id, scope_site=(scope == "site")
    )
    if existing is not None:
        if _now_refreshing(s, existing, now):
            entry["status"] = "cooldown"
            return entry
        # Retire the previous latest row so exactly one is_latest row
        # exists per (owner, domain, path, query) series — same invariant
        # the form path maintains — and drop its queue job so a retired
        # row is never crawled behind the user's back.
        existing.is_latest = False
        existing.key = None
        if getattr(existing, "queue_status", None) == "pending":
            existing.queue_status = None
        # Land the retire before the admitted insert shares the
        # uq_crawls_series_latest key with it.
        s.flush()
    row = Crawl(
        url=canon_url,
        domain=dom,
        path=path,
        query=query,
        canonical_url=canon_url,
        visibility="private",
        status="pending",
        user_id=user_id,
        crawl_params=_site_limits() if scope == "site" else None,
        created_at=now,
        updated_at=now,
        queue_status="pending",
    )
    s.add(row)
    s.flush()
    budget.admitted += 1
    entry["crawl_id"] = row.id
    entry["status"] = "queued"
    return entry


@router.post("/api/v1/analyses")
async def create_analyses(request: Request) -> JSONResponse:
    """Bulk-submit URLs for analysis; each URL is admitted or rejected with
    its own reason — the batch never fails wholesale.

    Body: {"urls": ["https://a.com", ...], "scope": "page" | "site"}.
    Admitted crawls are queued on the durable crawl queue (survives
    restarts) and owned by the caller (private).
    """
    user_id = _require_bearer_user(request)
    await enforce_body_size(request)
    try:
        data = await request.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    urls, scope = _parse_bulk_body(data)

    now = datetime.now(UTC)
    budget = _BatchBudget(user_id)
    with get_session() as s:
        items = [_admit_one(s, user_id, raw, scope, budget, now) for raw in urls]

    try:
        from webapp.services.api_usage import record_usage

        record_usage(
            getattr(request.state, "bearer_api_key_id", None),
            urls_admitted=sum(1 for i in items if i.get("status") == "queued"),
            urls_rejected=sum(1 for i in items if i.get("status") != "queued"),
        )
    except Exception:
        pass
    _stamp_first_use(request)
    return JSONResponse(status_code=201, content={"items": items})


@router.get("/api/v1/analyses")
async def list_analyses(request: Request, cursor: str = "", limit: int = 50) -> dict:
    """List the caller's crawls newest-first (the pipeline view).

    Keyset pagination: pass the last item's id as ``?cursor=``.
    """
    user_id = _require_bearer_user(request)
    limit = max(1, min(limit, 100))
    with get_session() as s:
        q = (
            s.query(Crawl)
            .filter(Crawl.user_id == user_id)
            .order_by(Crawl.created_at.desc(), Crawl.id.desc())
        )
        if cursor:
            anchor = s.get(Crawl, cursor)
            if anchor and anchor.user_id == user_id:
                q = q.filter(
                    (Crawl.created_at < anchor.created_at)
                    | ((Crawl.created_at == anchor.created_at) & (Crawl.id < anchor.id))
                )
        rows = q.limit(limit + 1).all()
    items = [_crawl_summary(r) for r in rows[:limit]]
    next_cursor = rows[limit - 1].id if len(rows) > limit and rows else None
    _stamp_first_use(request)
    return {"items": items, "next_cursor": next_cursor}


@router.get("/api/v1/analyses/{crawl_id}")
async def get_analysis(request: Request, crawl_id: str) -> JSONResponse:
    """Site-side contract for an owned crawl (202 while in progress).

    The succeeded response carries only the site-side contract: current
    factors with weights, lens ratings, interpretation text, and
    answerability evidence. No crawl payload internals and no legacy
    simulation or manual-input fields.
    """
    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
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
    resp = JSONResponse(content=_analysis_contract(row))
    resp.headers["X-Robots-Tag"] = "noindex"
    _stamp_first_use(request)
    return resp
