"""Owner-run visual CTA check on the analysis result page.

Split from :mod:`webapp.routers.analysis` so the over-cap router shrinks
instead of growing; the router is included by analysis and shares its
helpers through function-level imports. The capture itself is
:mod:`webapp.services.visual_audit` — the same code path the v1 API uses.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from webapp.db import get_session
from webapp.services.visual_audit import (
    VisualCaptureFailed,
    VisualCaptureTimeout,
    VisualCaptureUnavailable,
    claim_run,
    get_stored,
    release_run,
    run_capture,
    store_audit,
)

router = APIRouter()


def _image_kind(kind: str) -> str:
    """Map an image kind to its stored column; 404 for anything else."""
    if kind == "screenshot":
        return "screenshot_png"
    if kind == "overlay":
        return "overlay_png"
    raise HTTPException(status_code=404, detail="Not found")


@router.post("/analysis/{ref}/visual")
async def run_visual_check(
    request: Request, ref: str, csrf_token: str | None = Form(None)
) -> RedirectResponse:
    """Capture the page and assess its primary call to action (owner-only).

    Runs the audit inline (bounded by the shared capture timeout) and
    stores one audit per crawl; the result page re-renders with the panel.
    The cooldown is enforced here, not only by a disabled button.
    """
    from webapp.utils.security import verify_request_csrf

    from .analysis import _require_owned_analysis

    verify_request_csrf(request, csrf_token)
    row = await _require_owned_analysis(request, ref)
    if row.status != "succeeded":
        return RedirectResponse(url=f"/analysis/{row.id}", status_code=303)
    if not claim_run(row.id):
        return RedirectResponse(
            url=f"/analysis/{row.id}?notice=visual_cooldown", status_code=303
        )
    try:
        audit = await run_capture(row)
    except VisualCaptureTimeout:
        release_run(row.id)
        return RedirectResponse(
            url=f"/analysis/{row.id}?notice=visual_timeout", status_code=303
        )
    except VisualCaptureFailed, VisualCaptureUnavailable:
        release_run(row.id)
        return RedirectResponse(
            url=f"/analysis/{row.id}?notice=visual_unavailable", status_code=303
        )
    with get_session() as s:
        store_audit(s, row, audit, claim=True)
    return RedirectResponse(
        url=f"/analysis/{row.id}?notice=visual_done", status_code=303
    )


@router.get("/analysis/{ref}/visual/image/{kind}")
async def visual_image(request: Request, ref: str, kind: str) -> Response:
    """Serve one stored capture image (owner-only, never indexed)."""
    from .analysis import _require_owned_analysis

    column = _image_kind(kind)
    row = await _require_owned_analysis(request, ref)
    with get_session() as s:
        stored = get_stored(s, row.id)
        data = getattr(stored, column) if stored is not None else None
    if not data:
        raise HTTPException(status_code=404, detail="Not found")
    resp = Response(content=data, media_type="image/png")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp
