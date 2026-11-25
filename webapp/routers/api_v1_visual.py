"""The visual CTA assessment endpoint of the v1 API.

Split from :mod:`webapp.routers.api_v1` so the over-cap v1 module shrinks
instead of growing; the router is included by api_v1 and shares its helpers.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from meshweave.visual_audit import render_overlay, run_visual_audit
from webapp.models import Crawl
from webapp.routers.submissions import _require_page_url

router = APIRouter()
logger = logging.getLogger(__name__)

VISUAL_TIMEOUT_SECONDS = 20.0
VISUAL_MAX_CONCURRENT = 4
_capture_slots = asyncio.Semaphore(VISUAL_MAX_CONCURRENT)


@router.post("/api/v1/analyses/{crawl_id}/visual")
async def create_visual(
    request: Request, crawl_id: str, images: bool = False
) -> JSONResponse:
    """Capture the crawl's page and assess its primary call to action.

    Returns measured contrast, target size, labelling, occlusion, keyboard
    focus, competing actions, and — when a FastSal model is configured — the
    action's predicted attention share. ``images=true`` adds base64 PNGs for
    the screenshot and the attention overlay.
    """
    from .api_v1 import _owned_crawl, _require_bearer_user, _stamp_first_use

    user_id = _require_bearer_user(request)
    row = _owned_crawl(user_id, crawl_id)
    _require_page_url(row.url)
    if row.status != "succeeded":
        raise HTTPException(status_code=409, detail="Analysis is not ready")
    try:
        async with _capture_slots:
            audit = await run_visual_audit(
                row.url,
                cta_text=_stored_cta_text(row),
                cdp_endpoint=os.environ.get("MESHWEAVE_VISUAL_CDP_ENDPOINT"),
                timeout=VISUAL_TIMEOUT_SECONDS,
            )
    except PlaywrightTimeoutError:
        logger.warning("visual capture timed out for %s", row.id)
        raise HTTPException(
            status_code=504, detail="Visual capture timed out"
        ) from None
    except PlaywrightError as exc:
        logger.warning("visual capture failed for %s: %s", row.id, exc)
        raise HTTPException(status_code=502, detail="Visual capture failed") from exc
    except RuntimeError as exc:
        logger.warning("visual capture failed for %s: %s", row.id, exc)
        raise HTTPException(
            status_code=503, detail="Visual capture is not available"
        ) from exc

    payload: dict[str, Any] = {"id": row.id, **audit.to_dict()}
    if images:
        payload["screenshot_png"] = base64.b64encode(
            audit.snapshot.screenshot_png
        ).decode("ascii")
        overlay = render_overlay(audit)
        if overlay is not None:
            payload["overlay_png"] = base64.b64encode(overlay).decode("ascii")
    resp = JSONResponse(content=payload)
    resp.headers["X-Robots-Tag"] = "noindex"
    _stamp_first_use(request)
    return resp


def _stored_cta_text(row: Crawl) -> str | None:
    """The homepage action label from the stored AAX result, when present."""
    snapshot = row.score_snapshot
    analysis = snapshot.ai_analysis_json if snapshot is not None else None
    home = ((analysis or {}).get("aax") or {}).get("homepage_comprehension") or {}
    value = home.get("call_to_action")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
