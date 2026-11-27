"""The visual CTA assessment endpoints of the v1 API.

Split from :mod:`webapp.routers.api_v1` so the over-cap v1 module shrinks
instead of growing; the router is included by api_v1 and shares its helpers.
Capture, persistence, and payload shapes live in
:mod:`webapp.services.visual_audit` so the result-page runner reports the
same numbers.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from webapp.db import get_session
from webapp.services.visual_audit import (
    VisualCaptureFailed,
    VisualCaptureTimeout,
    VisualCaptureUnavailable,
    get_stored,
    payload_from,
    run_capture,
    store_audit,
)

router = APIRouter()


def _images(stored: Any) -> dict[str, str]:
    """Base64 PNGs from a stored audit (overlay only when captured)."""
    out = {"screenshot_png": base64.b64encode(stored.screenshot_png).decode("ascii")}
    if stored.overlay_png:
        out["overlay_png"] = base64.b64encode(stored.overlay_png).decode("ascii")
    return out


def _response(stored: Any, images: bool) -> JSONResponse:
    payload: dict[str, Any] = payload_from(stored)
    if images:
        payload.update(_images(stored))
    resp = JSONResponse(content=payload)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/api/v1/analyses/{crawl_id}/visual")
async def get_visual(
    request: Request, crawl_id: str, images: bool = False
) -> JSONResponse:
    """The stored visual CTA assessment for an owned crawl.

    202 while the analysis itself is still running (nothing captured yet),
    404 when a succeeded analysis has no stored capture — ``POST`` runs one.
    """
    from .api_v1 import _owned_crawl, _require_bearer_user, _stamp_first_use

    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    with get_session() as s:
        stored = get_stored(s, row.id)
        if stored is None:
            if row.status != "succeeded":
                return JSONResponse(
                    content={
                        "id": row.id,
                        "status": row.status,
                        "detail": "Analysis is not ready",
                    },
                    status_code=202,
                )
            raise HTTPException(status_code=404, detail="No visual audit stored")
        resp = _response(stored, images)
    _stamp_first_use(request)
    return resp


@router.post("/api/v1/analyses/{crawl_id}/visual")
async def create_visual(
    request: Request, crawl_id: str, images: bool = False
) -> JSONResponse:
    """Capture the crawl's page and assess its primary call to action.

    Runs (or re-runs) the capture and stores one audit per crawl. Returns
    measured contrast, target size, labelling, occlusion, keyboard focus,
    competing actions, and — when a FastSal model is configured — the
    action's predicted attention share. ``images=true`` adds base64 PNGs
    for the screenshot and the attention overlay.
    """
    from .api_v1 import _owned_crawl, _require_bearer_user, _stamp_first_use
    from .submissions import _require_page_url

    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    _require_page_url(row.url)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis is not ready")
    try:
        audit = await run_capture(row)
    except VisualCaptureTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except VisualCaptureFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except VisualCaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    with get_session() as s:
        stored = store_audit(s, row, audit)
        resp = _response(stored, images)
    _stamp_first_use(request)
    return resp
