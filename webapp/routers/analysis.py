import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import joinedload

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl
from webapp.routers.analysis_diff import router as _diff_router
from webapp.routers.analysis_visual import router as _visual_router
from webapp.services import nudges as nudges_svc
from webapp.services.visual_audit import (
    get_stored as get_visual_audit,
)
from webapp.services.visual_audit import (
    panel_context as visual_panel_context,
)
from webapp.services.visual_audit import (
    visual_export_context,
)
from webapp.utils.auth import require_ownership
from webapp.utils.diff import find_previous_revision, list_revision_series
from webapp.utils.export import (
    build_export_context,
    render_export_markdown,
    safe_filename,
)
from webapp.utils.result_meta import (
    analysis_json_ld,
    build_factor_extremes,
    email_source_map,
    meta_description,
    page_title,
    payload_scope,
    seo_title_desc,
)
from webapp.utils.scoring import (
    _sorted_recommendations,
)
from webapp.utils.scoring import (
    build_score_snapshot_context as _build_score_snapshot_context,
)
from webapp.utils.security import page_csrf, set_csrf_session_cookie
from webapp.utils.times import cooldown
from webapp.utils.url import _abs_url
from webapp.utils.viewer_state import (
    claim_min_hours,
    claim_state,
    compute_viewer_role,
    progress_for,
    public_refresh_state,
    save_offer_state,
)

router = APIRouter()
router.include_router(_diff_router)
router.include_router(_visual_router)


def _is_uuid(ref: str) -> bool:
    """Whether the ref parses as a UUID (private analysis id)."""
    try:
        _ = uuid.UUID(ref)
        return True
    except Exception:
        return False


def _revision_series_for(row: Crawl) -> list[dict]:
    """Revision strip entries for the owner view (succeeded rows only)."""
    if row.status != "succeeded":
        return []
    return list_revision_series(row)


def _since_last_run(row: Crawl) -> dict | None:
    """Signed lens deltas against the previous succeeded revision.

    Returns None when this run is the first in its series or either
    snapshot lacks scores. Deltas round to one decimal; the actionability
    composite is omitted when the previous revision has no composite yet.
    """
    if row.status != "succeeded":
        return None
    prev = find_previous_revision(row)
    if prev is None or prev.score_snapshot is None:
        return None

    def _delta(current: float | None, old: float | None) -> float | None:
        if current is None or old is None:
            return None
        return round(float(current) - float(old), 1)

    deltas = {
        "aeo": _delta(row.aeo_score, prev.score_snapshot.aeo_score),
        "geo": _delta(row.geo_score, prev.score_snapshot.geo_score),
    }
    prev_aax = (prev.score_snapshot.score_json or {}).get("aax", {}).get("composite")
    cur_aax = (
        (row.score_snapshot.score_json or {}).get("aax", {}).get("composite")
        if row.score_snapshot
        else None
    )
    deltas["aax"] = _delta(cur_aax, prev_aax)

    if all(d is None for d in deltas.values()):
        return None
    return {
        "previous_id": prev.id,
        "deltas": deltas,
    }


def _funnel_nudge_context(
    current_user,
    surface: str,
    viewer_role: str | None = None,
    *,
    suppress: bool = False,
) -> dict:
    """Nudge selection + impression metric for the result page.

    The nudge never renders for anonymous viewers (the page's own stage-1
    CTA covers them) or when ``suppress`` says the page's primary CTA (the
    save offer) is already showing. Owners get nothing to save and no gate
    pitch — selection handles that via the viewer role.
    """
    if suppress or viewer_role == "anonymous_public":
        return {"funnel_nudge": None}
    return nudges_svc.funnel_context(
        getattr(current_user, "id", None), surface, viewer_role
    )


def _public_viewer_state(request: Request, row: Crawl, in_progress: bool) -> dict:
    """Viewer-role, claim, refresh, save-offer, and CTA-impression state."""
    min_hours = claim_min_hours()
    created_at_iso = (row.created_at or datetime.now(UTC)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ownerless = getattr(row, "user_id", None) is None
    can_refresh, refresh_eta = public_refresh_state(row, in_progress)
    current_user = getattr(request.state, "current_user", None)
    viewer_role = compute_viewer_role(current_user, row)
    claim_eligible, claim_eta = claim_state(row, min_hours)
    if viewer_role == "anonymous_public":
        try:
            from webapp.utils.metrics import signin_cta_shown

            signin_cta_shown.labels("result").inc()
        except Exception:
            pass
    return {
        "claim_min_hours": min_hours,
        "created_at": created_at_iso,
        "ownerless": ownerless,
        "can_refresh": can_refresh,
        "refresh_eta": refresh_eta,
        "viewer_role": viewer_role,
        "claim_eligible": claim_eligible,
        "claim_eta": claim_eta,
        **save_offer_state(request, row, current_user),
    }


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

    row, progress, aax = progress_for(row, finalize=True)
    in_progress = progress is not None
    payload: dict | None = row.payload_json
    title_from_payload, desc_from_payload = seo_title_desc(payload)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = payload_scope(payload, row)
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    doc_title = page_title(
        scope_val, domain_val, path_val, title_from_payload, site_name
    )
    meta_desc = meta_description(desc_from_payload)
    abs_page_url = _abs_url(request, f"/analysis/{row.id}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None
    json_ld, reason_stopped_label = analysis_json_ld(
        row, payload, in_progress, abs_page_url, str(row.id)
    )
    api_url = f"/api/analysis/private/{row.id}"
    abs_api_url = _abs_url(request, api_url)

    # Cooldown for retry
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    can_retry_cooldown, retry_eta = cooldown(row.updated_at, refresh_min_age_minutes)
    can_retry = (row.status != "running") and can_retry_cooldown

    # CSRF token for retry form (generate new session if missing and CSRF is enabled)
    csrf_token, session_id, new_session = page_csrf(request)

    _ss_private = None if in_progress else _build_score_snapshot_context(row)
    since_last_run = _since_last_run(row)
    with get_session() as s:
        visual = visual_panel_context(s, row)
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
            "viewer_role": "owner",
            "email_source_map": email_source_map(payload),
            # SEO/Sharing
            "page_title": doc_title,
            "meta_description": meta_desc,
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
            "factor_extremes": build_factor_extremes(_ss_private),
            "aax_pending": aax,
            "aax_status": row.aax_status,
            "in_progress": in_progress,
            "progress": progress,
            "revisions": _revision_series_for(row),
            "since_last_run": since_last_run,
            "visual_audit": visual,
            "notice": request.query_params.get("notice"),
            **_funnel_nudge_context(
                getattr(request.state, "current_user", None),
                "result",
                "owner",
            ),
        },
    )
    # Prevent indexing of private results
    resp.headers["X-Robots-Tag"] = "noindex"
    set_csrf_session_cookie(resp, session_id, new_session)
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
    row, progress, aax = progress_for(row, finalize=False)
    in_progress = progress is not None
    payload = row.payload_json
    title_from_payload, desc_from_payload = seo_title_desc(payload)
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    scope_val = payload_scope(payload, row)
    domain_val = (row.domain or "").strip()
    path_val = (row.path or "").strip() or "/"
    doc_title = page_title(
        scope_val, domain_val, path_val, title_from_payload, site_name
    )
    meta_desc = meta_description(desc_from_payload)
    abs_page_url = _abs_url(request, f"/analysis/{row.key}")
    og_image_url = os.getenv("OG_IMAGE_URL") or None

    json_ld, reason_stopped_label = analysis_json_ld(
        row, payload, in_progress, abs_page_url, str(row.key)
    )

    api_url = f"/api/analysis/public/{row.key}"
    abs_api_url = _abs_url(request, api_url)

    # CSRF token for refresh form (generate new session if missing and CSRF is enabled)
    csrf_token, session_id, new_session = page_csrf(request)

    current_user = getattr(request.state, "current_user", None)
    viewer_state = _public_viewer_state(request, row, in_progress)

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
            "email_source_map": email_source_map(payload),
            # Provide private id to owners for chat scoping
            "id": row.id,
            # SEO/Sharing
            "page_title": doc_title,
            "meta_description": meta_desc,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "site_name": site_name,
            "json_ld": json_ld,
            # Refresh cooldown
            "score_snapshot": _ss_public,
            "sorted_recommendations": _sorted_recommendations(_ss_public),
            "factor_extremes": build_factor_extremes(_ss_public),
            "aax_pending": aax,
            "aax_status": row.aax_status,
            "in_progress": in_progress,
            "progress": progress,
            **viewer_state,
            **_funnel_nudge_context(
                current_user,
                "result",
                viewer_state.get("viewer_role"),
                suppress=bool(viewer_state.get("can_save")),
            ),
        },
    )
    set_csrf_session_cookie(resp, session_id, new_session)

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


async def _require_owned_analysis(request: Request, ref: str) -> Crawl:
    """Load an owned crawl by UUID ref; 404 for anything else."""
    if not _is_uuid(ref):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        row = await require_ownership(request, ref)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            raise HTTPException(status_code=404, detail="Not found")
        raise
    return row


@router.post("/analysis/{ref}/visibility")
async def toggle_analysis_visibility(
    request: Request,
    ref: str,
    visibility: str = Form(...),
    csrf_token: str | None = Form(None),
) -> RedirectResponse:
    """Flip a report public or private (owner-only).

    The journey requires making a report public to share it or private
    again; visibility was previously fixed at submit time. Strangers get
    404 so a UUID's existence is never revealed.
    """
    from webapp.utils.security import verify_request_csrf

    verify_request_csrf(request, csrf_token)
    row = await _require_owned_analysis(request, ref)
    target = visibility.strip().lower()
    if target not in ("public", "private"):
        raise HTTPException(status_code=400, detail="visibility must be public|private")
    now = datetime.now(UTC)
    with get_session() as s:
        db_row = s.get(Crawl, row.id)
        if not db_row or db_row.user_id != row.user_id:
            raise HTTPException(status_code=404, detail="Not found")
        if db_row.visibility == target:
            return RedirectResponse(
                url=f"/analysis/{row.id}?notice=visibility_{target}", status_code=303
            )
        # Moving a row into the target-visibility series must keep the
        # one-latest invariant: retire that series' current latest or
        # the toggle mints a double-latest series (unique index, and
        # the one_or_none() lookups the submit paths rely on).
        from webapp.utils.revisions import retire_series_latest

        retire_series_latest(s, db_row, visibility=target)
        db_row.visibility = target
        if target == "private":
            # Private rows leave the community surfaces; keeping the short
            # key just changes the URL shape, so clear it to match form
            # submissions (private rows carry no key).
            db_row.key = None
        else:
            if not db_row.key:
                from webapp.utils.url import generate_short_key

                db_row.key = generate_short_key()
            db_row.listed = True
        db_row.updated_at = now
    return RedirectResponse(
        url=f"/analysis/{row.id}?notice=visibility_{target}", status_code=303
    )


@router.post("/analysis/{ref}/delete")
async def delete_analysis(
    request: Request,
    ref: str,
    csrf_token: str | None = Form(None),
) -> RedirectResponse:
    """Delete one owned analysis and every revision in its series.

    Strangers get 404 so a UUID's existence is never revealed. Only the
    owner's own runs are removed: a series keyed by domain/path/query also
    matches other users' runs (e.g. an ownerless public submission), and
    those rows are untouched.
    """
    from webapp.utils.security import verify_request_csrf

    verify_request_csrf(request, csrf_token)
    row = await _require_owned_analysis(request, ref)
    with get_session() as s:
        for target in (
            s.query(Crawl)
            .filter(
                Crawl.user_id == row.user_id,
                Crawl.domain == row.domain,
                Crawl.path == row.path,
                Crawl.query == row.query,
                Crawl.visibility == row.visibility,
            )
            .all()
        ):
            s.delete(target)
    return RedirectResponse(url="/dashboard?notice=analysis_deleted", status_code=303)


@router.get("/analysis/{ref}/export")
async def export_analysis(request: Request, ref: str) -> Response:
    """Download the private report as a Markdown artifact (owner-only).

    The route is a thin gate: UUID refs only, ownership (401/403 → 404), and a
    completeness check (incomplete → 409). All derivation lives in
    ``webapp.utils.export`` so the policy stays unit-testable.
    """
    if not _is_uuid(ref):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        row = await require_ownership(request, ref)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            raise HTTPException(status_code=404, detail="Not found")
        raise

    if (
        row.status != "succeeded"
        or not row.score_snapshot
        or row.aax_status != "completed"
    ):
        raise HTTPException(status_code=409, detail="Report is not ready")

    # The primary export path must be visible to the funnel
    from webapp.services import funnel

    with get_session() as s:
        funnel.emit(
            s,
            row.user_id,
            funnel.EVENT_REPORT_EXPORTED,
            crawl_id=row.id,
            domain=row.domain,
            format="markdown",
        )
        visual = visual_export_context(get_visual_audit(s, row.id))

    markdown = render_export_markdown(
        build_export_context(
            row,
            site_name=os.getenv("SITE_NAME", "MeshWeave"),
            contact_email=os.getenv("FOOTER_CONTACT_EMAIL", "hello@meshweaveai.com"),
            visual=visual,
        ),
    )
    response = Response(content=markdown, media_type="text/markdown; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="ai-agent-audit-{safe_filename(row.domain)}.md"'
    )
    return response
