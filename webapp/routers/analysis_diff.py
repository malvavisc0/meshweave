"""Revision comparison page for the analysis result.

Split from :mod:`webapp.routers.analysis` so the over-cap router shrinks
instead of growing; the router is included by analysis and shares its
helpers through function-level imports. Logic is unchanged.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.auth import require_ownership
from webapp.utils.diff import (
    CrossVersionComparisonError,
    build_content_diff,
    build_findings_diff,
    build_score_diff,
    ensure_same_scoring_version,
    find_previous_revision,
    list_revision_series,
)
from webapp.utils.url import _abs_url

router = APIRouter()


def diff_row_view(row: Crawl) -> dict:
    """Return the header fields shown for one revision in the diff page."""
    return {
        "id": row.id,
        "domain": row.domain,
        "path": row.path,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "scope": "site" if row.crawl_params is not None else "page",
    }


def diff_header_context(
    request: Request, row: Crawl, old_row: Crawl | None, is_default_compare: bool
) -> dict:
    """Shared diff-page context: run headers, page chrome, empty diff fields."""
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    domain = (row.domain or "").strip()
    path = (row.path or "").strip() or "/"
    return {
        "new": diff_row_view(row),
        "old": diff_row_view(old_row) if old_row else None,
        "has_old": old_row is not None and old_row.id != row.id,
        "is_default_compare": is_default_compare,
        "score_diff": None,
        "findings_diff": None,
        "content_diff": None,
        "comparison_refused": None,
        "revisions": list_revision_series(row),
        "page_title": f"Revision diff — {domain}{path} — {site_name}",
        "site_name": site_name,
        "csrf_token": "",
        "domain": domain,
        "path": path,
        "canonical_url": row.canonical_url,
        "abs_ref_url": _abs_url(request, f"/analysis/{row.id}"),
    }


def build_diff_context(
    request: Request, row: Crawl, old_row: Crawl | None, is_default_compare: bool
) -> dict:
    """Build the template context for the diff page.

    Three states: empty (no comparison run), full diff, and a hard
    refusal when the two runs were scored under different scoring
    versions — a cross-version pair is an error state, never a diff with
    an annotation.
    """
    ctx = diff_header_context(request, row, old_row, is_default_compare)
    if not ctx["has_old"] or old_row is None:
        return ctx
    old_ss = getattr(old_row, "score_snapshot", None)
    new_ss = getattr(row, "score_snapshot", None)
    try:
        ensure_same_scoring_version(old_row, row, old_ss, new_ss)
    except CrossVersionComparisonError as exc:
        return {**ctx, "comparison_refused": str(exc), "revisions": []}
    ctx["score_diff"] = build_score_diff(old_ss, new_ss, old_row, row)
    ctx["findings_diff"] = build_findings_diff(old_ss, new_ss)
    ctx["content_diff"] = build_content_diff(old_row.payload_json, row.payload_json)
    return ctx


async def resolve_vs_row(request: Request, vs: str, base_row: Crawl) -> Crawl | None:
    """Load and validate the ``?vs=`` row; None on any mismatch.

    The vs row must be owned by the current user, be a succeeded run, and
    share the base row's domain, path, query, and scope (crawl_params
    presence). Any failure — missing, non-owner, non-succeeded, mismatched
    address — collapses to None so the caller 404s uniformly without
    revealing whether the row exists.
    """
    try:
        vs_row = await require_ownership(request, vs)
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            return None
        raise
    if (
        vs_row.status != "succeeded"
        or vs_row.domain != base_row.domain
        or vs_row.path != base_row.path
        or vs_row.query != base_row.query
        or (vs_row.crawl_params is not None) != (base_row.crawl_params is not None)
    ):
        return None
    return vs_row


@router.get("/analysis/{ref}/diff", response_class=HTMLResponse)
async def view_analysis_diff(request: Request, ref: str, vs: str | None = None):
    """Compare the current revision against a previous one (owner-only).

    ``ref`` is a UUID addressed by the owner. ``?vs=`` overrides the default
    previous-revision comparison and must be another owned row in the same
    series. Unauthenticated, non-owner, or mismatched refs collapse to 404.
    """
    from .analysis import _is_uuid, _require_owned_analysis

    target = vs or ref
    if not _is_uuid(target):
        raise HTTPException(status_code=404, detail="Not found")

    row = await _require_owned_analysis(request, ref)

    # In-progress base rows are handled by the result page's meta-refresh loop.
    if str(row.status or "").lower() in ("pending", "running"):
        return RedirectResponse(url=f"/analysis/{ref}", status_code=303)

    is_default_compare = False
    if vs:
        old_row = await resolve_vs_row(request, vs, row)
        if old_row is None:
            raise HTTPException(status_code=404, detail="Not found")
    else:
        old_row = find_previous_revision(row)
        is_default_compare = True

    context = build_diff_context(request, row, old_row, is_default_compare)
    resp = templates.TemplateResponse(request, "diff.html", context)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp
