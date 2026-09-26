"""Versioned JSON API (v1) — the surface the funnel's gates point at.

Bearer-key authenticated only (no browser sessions; session-based access to
these resources is a separate, deliberately deferred decision). All
resources are owner-scoped: a key only ever sees its owner's crawls.

Endpoints:
- POST /api/v1/analyses — bulk submit (page or site), durable queue
- GET  /api/v1/analyses — the caller's crawl list, cursor-paginated
- GET  /api/v1/analyses/{crawl_id} — full private payload
- GET  /api/v1/analyses/{crawl_id}/report.md — unbranded client-ready report
- GET  /api/v1/analyses/{crawl_id}/diff[.md] — proof-of-work diff
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from webapp.db import get_session
from webapp.models import Crawl
from webapp.routers.api import _bearer_user_id
from webapp.routers.submissions import (
    _find_latest_crawl,
    _normalize_domain_field,
    _now_refreshing,
    _require_page_url,
)
from webapp.services import funnel
from webapp.utils.diff import (
    build_findings_diff,
    build_score_diff,
    find_previous_revision,
)
from webapp.utils.export import (
    build_export_context,
    render_export_markdown,
    safe_filename,
)
from webapp.utils.quotas import (
    _concurrent_jobs_limit,
    _count_user_concurrent_jobs,
    _count_user_daily_site_crawls,
    _daily_site_limit,
)
from webapp.utils.url import canonicalize_url, reject_internal_target

router = APIRouter()

BULK_BATCH_CAP = 25
BULK_BODY_MAX_BYTES = 64 * 1024


# ── helpers ─────────────────────────────────────────────────────────


def _require_bearer_user(request: Request) -> str:
    """Resolve the caller's user id from a Bearer key or raise 401.

    Counts the call against the key's daily usage row (best-effort) so the
    deferred rate-limit decision has real p50/p95 data to work with.
    """
    user_id = _bearer_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="API key required")
    try:
        from webapp.services.api_usage import record_usage

        record_usage(getattr(request.state, "bearer_api_key_id", None), calls=1)
    except Exception:
        pass
    return user_id


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
    existing = _find_latest_crawl(s, "private", dom, path, query, user_id)
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
    if not budget.allows(scope):
        entry["status"] = "quota"
        return entry
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
    _enforce_body_size(request)
    try:
        data = await request.json()
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
    return JSONResponse(status_code=201, content={"items": items})


def _enforce_body_size(request: Request) -> None:
    """Reject oversized bulk bodies before parsing (HTTP 413)."""
    raw = request.headers.get("content-length")
    if not raw:
        return
    try:
        size = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length")
    if size > BULK_BODY_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")


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
    return {"items": items, "next_cursor": next_cursor}


@router.get("/api/v1/analyses/{crawl_id}")
async def get_analysis(request: Request, crawl_id: str) -> JSONResponse:
    """Full private payload for an owned crawl (202 while in progress)."""
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
    resp = JSONResponse(content=row.payload_json or {})
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


# ── D2: unbranded report export ─────────────────────────────────────


@router.get("/api/v1/analyses/{crawl_id}/report.md")
async def get_report_markdown(request: Request, crawl_id: str) -> PlainTextResponse:
    """Client-ready, unbranded Markdown report for an owned crawl."""
    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis not finished")
    # Unbranded: no MeshWeave header branding, no MeshWeave contact footer.
    ctx = build_export_context(row, site_name="", contact_email="")
    body = render_export_markdown(ctx)
    filename = f"ai-visibility-report-{safe_filename(row.domain)}.md"
    resp = PlainTextResponse(content=body, media_type="text/markdown")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    with get_session() as s:
        funnel.emit(
            s,
            user_id,
            funnel.EVENT_REPORT_EXPORTED,
            crawl_id=row.id,
            domain=row.domain,
            format="markdown",
        )
    return resp


# ── D3: proof diff export ───────────────────────────────────────────


def _resolve_vs_owned(user_id: str, row: Crawl, vs: str) -> Crawl | None:
    """Resolve the comparison row: explicit ``?vs=`` (owned, succeeded, same
    series) or the default previous revision. None collapses to 404."""
    if vs:
        vs_row = _owned_crawl(user_id, vs)
        if vs_row.status != "succeeded":
            raise HTTPException(status_code=404, detail="Not found")
        if (
            vs_row.domain != row.domain
            or vs_row.path != row.path
            or vs_row.query != row.query
            or bool(vs_row.crawl_params) != bool(row.crawl_params)
        ):
            raise HTTPException(status_code=404, detail="Not found")
        return vs_row
    old_row: Crawl | None = find_previous_revision(row)
    if old_row and old_row.user_id != user_id:
        old_row = None
    return old_row


def _diff_payload(row: Crawl, old_row: Crawl | None) -> dict[str, Any]:
    old_ss = getattr(old_row, "score_snapshot", None) if old_row else None
    new_ss = getattr(row, "score_snapshot", None)
    score_diff = build_score_diff(old_ss, new_ss, old_row, row) if old_row else None
    findings_diff = build_findings_diff(old_ss, new_ss) if old_row else None
    return {
        "crawl_id": row.id,
        "vs_crawl_id": old_row.id if old_row else None,
        "domain": row.domain,
        # Honesty: predicted deltas are per fix; observed deltas are per
        # lens (aggregate), never per fix.
        "delta_granularity": {"predicted": "per_fix", "observed": "per_lens"},
        "score_diff": score_diff,
        "findings_diff": findings_diff,
    }


@router.get("/api/v1/analyses/{crawl_id}/diff")
async def get_diff(request: Request, crawl_id: str, vs: str = "") -> dict:
    """Proof-of-work diff vs the previous revision (or ``?vs=<crawl_id>``)."""
    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis not finished")
    old_row = _resolve_vs_owned(user_id, row, vs)
    return _diff_payload(row, old_row)


@router.get("/api/v1/analyses/{crawl_id}/diff.md")
async def get_diff_markdown(
    request: Request, crawl_id: str, vs: str = ""
) -> PlainTextResponse:
    """Markdown evidence pack: before/after scores + resolved fixes."""
    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis not finished")
    old_row = _resolve_vs_owned(user_id, row, vs)
    body = _render_diff_markdown(_diff_payload(row, old_row), row, old_row)
    filename = f"ai-visibility-diff-{safe_filename(row.domain)}.md"
    resp = PlainTextResponse(content=body, media_type="text/markdown")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    with get_session() as s:
        funnel.emit(
            s,
            user_id,
            funnel.EVENT_DIFF_EXPORTED,
            crawl_id=row.id,
            domain=row.domain,
            format="markdown",
        )
    return resp


def _md_score_table(composites: dict) -> list[str]:
    """The before/after score table rows (observed per lens)."""
    lines = [
        "## Scores (observed per lens)",
        "",
        "| Lens | Before | After | Δ |",
        "| --- | --- | --- | --- |",
    ]
    for lens in ("aeo", "geo", "aax"):
        d = composites.get(lens) or {}
        lines.append(
            f"| {lens.upper()} | {_fmt_score(d.get('old'))} "
            f"| {_fmt_score(d.get('new'))} | {_fmt_delta(d.get('delta'))} |"
        )
    lines.append("")
    return lines


def _md_resolved_fixes(resolved: list) -> list[str]:
    """The resolved-findings list (predicted per fix)."""
    lines = ["## Resolved fixes (predicted per fix)", ""]
    if not resolved:
        lines.append("_No resolved findings in this comparison._")
        return lines
    for rec in resolved:
        title = rec.get("title") or "Untitled"
        exp = rec.get("expected_points")
        exp_txt = f" (+{exp} predicted)" if exp is not None else ""
        lines.append(f"- **{title}**{exp_txt}")
    return lines


def _render_diff_markdown(payload: dict, row: Crawl, old_row: Crawl | None) -> str:
    """Serialize the diff as a clean Markdown evidence pack."""
    lines = [f"# AI Visibility Progress Report — {row.domain}", ""]
    if not old_row:
        lines.append("_No previous revision to compare against yet._")
        lines.append("")
        return "\n".join(lines)
    lines.append(
        f"Comparing run `{old_row.id[:8]}` → `{row.id[:8]}` "
        f"({_fmt_date(old_row.created_at)} → {_fmt_date(row.created_at)})."
    )
    lines.append("")
    composites = (payload.get("score_diff") or {}).get("composites") or {}
    lines.extend(_md_score_table(composites))
    resolved = (payload.get("findings_diff") or {}).get("resolved") or []
    lines.extend(_md_resolved_fixes(resolved))
    lines.append("")
    lines.append(
        "_Predicted score changes are estimated per fix; observed changes"
        " are measured per lens (aggregate across all fixes in that lens)._"
    )
    lines.append("")
    return "\n".join(lines)


def _fmt_date(dt: Any) -> str:
    try:
        return str(dt.strftime("%Y-%m-%d"))
    except Exception:
        return ""


def _fmt_score(v) -> str:
    return "—" if v is None else str(round(float(v), 1))


def _fmt_delta(v) -> str:
    if v is None:
        return "—"
    f = float(v)
    return f"+{round(f, 1)}" if f > 0 else str(round(f, 1))
