"""Viewer, claim, and refresh state for the analysis result page.

Split from :mod:`webapp.routers.analysis` so the over-cap router shrinks
instead of growing. Logic is unchanged.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from fastapi import Request

from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.progress import progress_view
from webapp.utils.scoring import aax_pending
from webapp.utils.times import ensure_utc


def claim_min_hours() -> int:
    """Configured minimum age (hours) before a public analysis can be claimed."""
    try:
        return int(os.getenv("CLAIM_PUBLIC_MIN_AGE_HOURS", "24"))
    except Exception:
        return 24


def compute_viewer_role(current_user, row: Crawl) -> str:
    """Return the explicit viewer role for the result template.

    One of: owner | ownerless_public | public_non_owner | anonymous_public.
    Ownership is derived from the row's user_id, never inferred from the
    presence of a logged-in user alone.
    """
    if current_user and getattr(row, "user_id", None) == current_user.id:
        return "owner"
    if current_user is None:
        return "anonymous_public"
    return (
        "ownerless_public"
        if getattr(row, "user_id", None) is None
        else "public_non_owner"
    )


def progress_for(row: Crawl, finalize: bool) -> tuple[Crawl, dict | None, bool]:
    """Build SSR progress for in-flight crawls.

    Returns (row, progress_or_None, aax_pending_value). finalize should be
    False for non-owner renders so viewers cannot trigger stale finalization.
    """
    aax = aax_pending(row)
    if str(row.status or "").lower() in ("pending", "running") or aax:
        row, progress = progress_view(row, finalize=finalize)
        return row, progress, aax
    return row, None, aax


def claim_state(row: Crawl, min_hours: int) -> tuple[bool, str]:
    """Return (eligible, eta_label) for claiming an ownerless public row.

    A row is claimable when it has no owner and its created_at is at least
    min_hours in the past. eta_label is empty when eligible.
    """
    if getattr(row, "user_id", None) is not None:
        return False, ""
    created = getattr(row, "created_at", None)
    if created is None:
        return False, ""
    created = ensure_utc(created)
    eligible = created + timedelta(hours=min_hours) <= datetime.now(UTC)
    if eligible:
        return True, ""
    remaining = int(
        (created + timedelta(hours=min_hours) - datetime.now(UTC)).total_seconds()
        / 3600
    )
    return False, f"{max(0, remaining)}h"


def public_refresh_state(row: Crawl, in_progress: bool) -> tuple[bool, str]:
    """Compute (can_refresh, refresh_eta) against the public domain-root cooldown."""
    # Cooldown for refresh (public domain root); irrelevant mid-crawl
    can_refresh = False
    refresh_eta = ""
    if in_progress:
        return can_refresh, refresh_eta
    refresh_min_age_minutes = int(os.getenv("REFRESH_MIN_AGE_MINUTES", "60"))
    now = datetime.now(UTC)
    with get_session() as s:
        public_root = (
            s.query(Crawl)
            .filter(
                Crawl.domain == row.domain,
                Crawl.visibility == "public",
                Crawl.path == "/",
                Crawl.query == "",
            )
            .first()
        )
    if not public_root:
        return can_refresh, refresh_eta
    next_refresh_eligible = ensure_utc(public_root.updated_at) + timedelta(
        minutes=refresh_min_age_minutes
    )
    can_refresh = now >= next_refresh_eligible
    if not can_refresh:
        refresh_eta = f"{int((next_refresh_eligible - now).total_seconds() / 60)}m"
    return can_refresh, refresh_eta


def save_offer_state(request: Request, row: Crawl, current_user) -> dict:
    """Inputs for the result page's "Save to my account" offer.

    The offer shows only when the viewer is authenticated, the crawl is
    ownerless, and this browser's anonymous cookie matches the crawl's
    ``anonymous_user_id`` — provable per-analysis attribution.
    """
    ownerless = getattr(row, "user_id", None) is None
    anon_cookie = request.cookies.get(
        os.getenv("WEBAPP_ANON_ID_COOKIE_NAME", "mw_anon_id")
    )
    cookie_match = bool(anon_cookie and row.anonymous_user_id == anon_cookie)
    return {
        "can_save": bool(current_user and ownerless and cookie_match),
        "anon_id": anon_cookie or "",
    }
