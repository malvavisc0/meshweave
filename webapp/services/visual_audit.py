"""Capture, persist, and serve one visual CTA assessment per crawl.

The v1 API and the result-page runner share this service so both surfaces
run the same capture (same semaphore, same timeout, same error taxonomy)
and the stored audit is the single source for re-renders, exports, and
``GET /api/v1/analyses/{crawl_id}/visual``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.orm import Session

from meshweave.visual_audit import (
    VISUAL_CAPTURE_VERSION,
    VisualAudit,
    render_overlay,
    run_visual_audit,
)
from webapp.db import get_session
from webapp.models import Crawl
from webapp.models_visual import VisualAudit as VisualAuditRow
from webapp.utils.times import cooldown

logger = logging.getLogger(__name__)

VISUAL_TIMEOUT_SECONDS = 20.0
VISUAL_MAX_CONCURRENT = 4
_capture_slots = asyncio.Semaphore(VISUAL_MAX_CONCURRENT)


def _min_age_minutes() -> int:
    """Owner run cooldown from ``VISUAL_MIN_AGE_MINUTES`` (default 10)."""
    return int(os.getenv("VISUAL_MIN_AGE_MINUTES", "10"))


class VisualCaptureTimeout(Exception):
    """The browser did not produce a capture within the timeout."""


class VisualCaptureFailed(Exception):
    """The capture ran but the browser could not complete it."""


class VisualCaptureUnavailable(Exception):
    """No capture service is reachable."""


def stored_cta_text(row: Crawl) -> str | None:
    """The homepage action label from the stored AAX result, when present."""
    snapshot = row.score_snapshot
    analysis = snapshot.ai_analysis_json if snapshot is not None else None
    home = ((analysis or {}).get("aax") or {}).get("homepage_comprehension") or {}
    value = home.get("call_to_action")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def run_capture(row: Crawl) -> VisualAudit:
    """Capture and assess the crawl's page through the shared slot pool.

    Raises :class:`VisualCaptureTimeout`, :class:`VisualCaptureFailed`, or
    :class:`VisualCaptureUnavailable` so every caller maps failures the
    same way.
    """
    try:
        async with _capture_slots:
            return await run_visual_audit(
                row.url,
                cta_text=stored_cta_text(row),
                cdp_endpoint=os.environ.get("MESHWEAVE_VISUAL_CDP_ENDPOINT"),
                timeout=VISUAL_TIMEOUT_SECONDS,
            )
    except PlaywrightTimeoutError:
        logger.warning("visual capture timed out for %s", row.id)
        raise VisualCaptureTimeout("Visual capture timed out") from None
    except PlaywrightError as exc:
        logger.warning("visual capture failed for %s: %s", row.id, exc)
        raise VisualCaptureFailed("Visual capture failed") from exc
    except RuntimeError as exc:
        logger.warning("visual capture unavailable for %s: %s", row.id, exc)
        raise VisualCaptureUnavailable("Visual capture is not available") from exc


def store_audit(s: Session, row: Crawl, audit: VisualAudit) -> VisualAuditRow:
    """Persist one audit per crawl (a re-run replaces the row) and emit.

    The crawl row is locked for the store, so two captures of the same
    crawl (a retried API POST, an owner run racing the API) replace each
    other instead of racing the one-row-per-crawl constraint. Storing
    clears any claim and stamps ``updated_at`` now. The
    ``visual_audit_run`` funnel event shares ``s``'s transaction.
    """
    snapshot = audit.snapshot
    values = {
        "url": snapshot.final_url,
        "assessment_json": audit.assessment.to_dict(),
        "attention_model": audit.attention is not None,
        "viewport": {
            "width": snapshot.viewport_width,
            "height": snapshot.viewport_height,
        },
        "element_count": len(snapshot.elements),
        "screenshot_png": snapshot.screenshot_png,
        "overlay_png": render_overlay(audit),
        "capture_version": VISUAL_CAPTURE_VERSION,
        "claimed_at": None,
        "updated_at": datetime.now(UTC),
    }
    from webapp.services import funnel

    s.query(Crawl).filter(Crawl.id == row.id).with_for_update().one()
    stored = (
        s.query(VisualAuditRow).filter(VisualAuditRow.crawl_id == row.id).one_or_none()
    )
    if stored is None:
        stored = VisualAuditRow(crawl_id=row.id, **values)
        s.add(stored)
    else:
        for key, value in values.items():
            setattr(stored, key, value)
    funnel.emit(
        s,
        row.user_id,
        funnel.EVENT_VISUAL_AUDIT_RUN,
        crawl_id=row.id,
        domain=row.domain,
    )
    s.flush()
    return stored


def claim_run(crawl_id: str) -> bool:
    """Claim an owner run unless a capture is in flight or cooling down.

    The owner form runs on the app's single event loop, so the read and
    the write cannot interleave with another owner claim. A claim is
    live until the cooldown window passes — an older claim is a crashed
    capture (the process died mid-run) and is claimed again. Claims
    never touch stored data: a claim blocks another claim, it does not
    hide the last successful capture.
    """
    now = datetime.now(UTC)
    min_age = _min_age_minutes()
    with get_session() as s:
        stored = (
            s.query(VisualAuditRow)
            .filter(VisualAuditRow.crawl_id == crawl_id)
            .one_or_none()
        )
        if stored is not None:
            if (
                stored.claimed_at is not None
                and not cooldown(stored.claimed_at, min_age)[0]
            ):
                return False
            if not cooldown(stored.updated_at, min_age)[0]:
                return False
            stored.claimed_at = now
            s.flush()
            return True
        s.add(VisualAuditRow(crawl_id=crawl_id, claimed_at=now))
        s.flush()
        return True


def release_run(crawl_id: str) -> None:
    """Release a claim after a failed capture so a retry is allowed.

    A claim row for a never-captured crawl is deleted (nothing was
    stored); a claim over a stored audit just drops the claim, so the
    last successful capture stays readable and the owner can retry
    immediately.
    """
    with get_session() as s:
        stored = (
            s.query(VisualAuditRow)
            .filter(VisualAuditRow.crawl_id == crawl_id)
            .one_or_none()
        )
        if stored is None or stored.claimed_at is None:
            return
        if stored.screenshot_png is None:
            s.delete(stored)
        else:
            stored.claimed_at = None
        s.flush()


def get_stored(s: Session, crawl_id: str) -> VisualAuditRow | None:
    """The stored audit for a crawl; None when no capture ever landed.

    Reads are decided by data presence only: while an owner re-run is in
    flight the last successful capture stays readable on every surface.
    """
    stored = (
        s.query(VisualAuditRow)
        .filter(VisualAuditRow.crawl_id == crawl_id)
        .one_or_none()
    )
    if stored is None or stored.screenshot_png is None:
        return None
    return stored


def payload_from(stored: VisualAuditRow) -> dict[str, Any]:
    """The public JSON shape of a stored audit (images stay out)."""
    assert stored.screenshot_png is not None  # only captured rows are served
    return {
        "id": stored.crawl_id,
        "url": stored.url,
        "viewport": stored.viewport,
        "element_count": stored.element_count,
        "screenshot_bytes": len(stored.screenshot_png),
        "attention_model": stored.attention_model,
        "assessment": stored.assessment_json,
        "capture_version": stored.capture_version,
    }


def visual_export_context(stored: VisualAuditRow | None) -> dict[str, Any] | None:
    """The report.md 'CTA visual check' section inputs; None when uncaptured."""
    if stored is None:
        return None
    assessment = dict(stored.assessment_json or {})
    return {
        "url": stored.url,
        "cta_text": assessment.get("cta_text"),
        "matched_by": assessment.get("matched_by"),
        "attention_model": stored.attention_model,
        "contrast_ratio": assessment.get("contrast_ratio"),
        "contrast_threshold": assessment.get("contrast_threshold"),
        "surface_contrast": assessment.get("surface_contrast"),
        "target_px": assessment.get("target_px"),
        "competing_actions": assessment.get("competing_actions"),
        "competing_names": list(assessment.get("competing_names") or []),
        "attention_share": assessment.get("attention_share"),
        "attention_rank": assessment.get("attention_rank"),
        "findings": list(assessment.get("findings") or []),
    }


def panel_context(s: Session, row: Crawl) -> dict[str, Any]:
    """Result-page inputs for the visual CTA panel (owner block).

    States the panel renders honestly: never run, captured (with or
    without a configured attention model), and re-run availability.
    """
    stored = get_stored(s, row.id)
    context: dict[str, Any] = {
        "stored": stored is not None,
        "payload": payload_from(stored) if stored is not None else None,
        "image_url": (
            f"/analysis/{row.id}/visual/image/screenshot" if stored else None
        ),
        "overlay_url": (
            f"/analysis/{row.id}/visual/image/overlay"
            if stored is not None and stored.overlay_png
            else None
        ),
    }
    if stored is None:
        context["can_run"] = True
        context["run_eta"] = ""
        return context
    context["can_run"], context["run_eta"] = cooldown(
        stored.updated_at, _min_age_minutes()
    )
    return context
