"""Report and diff exports of the v1 API.

Split from :mod:`webapp.routers.api_v1` so the over-cap v1 module shrinks
instead of growing (same split as ``api_v1_visual``); the router is
included by api_v1 and shares its helpers through function-level imports.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from webapp.db import get_session
from webapp.models import Crawl
from webapp.services.visual_audit import get_stored, visual_export_context
from webapp.utils.diff import (
    CrossVersionComparisonError,
    build_findings_diff,
    build_score_diff,
    ensure_same_scoring_version,
    find_previous_revision,
)
from webapp.utils.export import (
    build_export_context,
    render_diff_markdown,
    render_export_markdown,
    safe_filename,
)

router = APIRouter()


# ── D2: unbranded report export ─────────────────────────────────────


@router.get("/api/v1/analyses/{crawl_id}/report.md")
async def get_report_markdown(request: Request, crawl_id: str) -> PlainTextResponse:
    """Client-ready, unbranded Markdown report for an owned crawl.

    Readiness matches the browser export: succeeded, snapshot
    present, and the async AAX analysis completed — a report labelled
    client-ready must not ship with "—" actionability and missing
    findings while AAX is still pending.
    """
    from .api_v1 import _owned_crawl, _require_bearer_user, _stamp_first_use

    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    if (
        row.status != "succeeded"
        or not row.score_snapshot
        or str(getattr(row, "aax_status", "") or "") != "completed"
    ):
        raise HTTPException(status_code=409, detail="Report is not ready")
    from webapp.services import funnel

    with get_session() as s:
        visual = visual_export_context(get_stored(s, row.id))
    # Unbranded: no MeshWeave header branding, no MeshWeave contact footer.
    ctx = build_export_context(row, site_name="", contact_email="", visual=visual)
    body = render_export_markdown(ctx)
    filename = f"ai-agent-audit-{safe_filename(row.domain)}.md"
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
    _stamp_first_use(request)
    return resp


# ── D3: proof diff export ───────────────────────────────────────────


def _resolve_vs_owned(user_id: str, row: Crawl, vs: str) -> Crawl | None:
    """Resolve the comparison row: explicit ``?vs=`` (owned, succeeded, same
    series) or the default previous revision. None collapses to 404.

    A resolved pair scored under different scoring versions is refused
    with 409 — cross-version comparison is never rendered, annotated or
    not.
    """
    from .api_v1 import _owned_crawl

    if vs:
        vs_row = _owned_crawl(user_id, vs)
        if vs_row.status != "succeeded":
            raise HTTPException(status_code=404, detail="Not found")
        if (
            vs_row.domain != row.domain
            or vs_row.path != row.path
            or vs_row.query != row.query
            or (vs_row.crawl_params is not None) != (row.crawl_params is not None)
        ):
            raise HTTPException(status_code=404, detail="Not found")
        old_row: Crawl | None = vs_row
    else:
        old_row = find_previous_revision(row)
        if old_row and old_row.user_id != user_id:
            old_row = None
    _refuse_cross_version(row, old_row)
    return old_row


def _refuse_cross_version(row: Crawl, old_row: Crawl | None) -> None:
    """Raise 409 when the two runs carry different scoring versions."""
    if old_row is None:
        return
    try:
        ensure_same_scoring_version(old_row, row)
    except CrossVersionComparisonError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    from .api_v1 import _owned_crawl, _require_bearer_user, _stamp_first_use

    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis not finished")
    old_row = _resolve_vs_owned(user_id, row, vs)
    _stamp_first_use(request)
    return _diff_payload(row, old_row)


@router.get("/api/v1/analyses/{crawl_id}/diff.md")
async def get_diff_markdown(
    request: Request, crawl_id: str, vs: str = ""
) -> PlainTextResponse:
    """Markdown evidence pack: before/after scores + resolved fixes."""
    from .api_v1 import _owned_crawl, _require_bearer_user, _stamp_first_use

    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis not finished")
    old_row = _resolve_vs_owned(user_id, row, vs)
    body = render_diff_markdown(_diff_payload(row, old_row), row, old_row)
    filename = f"ai-agent-audit-diff-{safe_filename(row.domain)}.md"
    resp = PlainTextResponse(content=body, media_type="text/markdown")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    from webapp.services import funnel

    with get_session() as s:
        funnel.emit(
            s,
            user_id,
            funnel.EVENT_DIFF_EXPORTED,
            crawl_id=row.id,
            domain=row.domain,
            format="markdown",
        )
    _stamp_first_use(request)
    return resp
