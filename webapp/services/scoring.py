"""Centralized AEO/GEO/AAX scoring service.

Wraps the scoring engine and ScoreSnapshot persistence into reusable
functions, eliminating duplication across crawling.py, site_crawling.py,
and scores.py router.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from meshweave.scoring.engine import compute_aax_score, compute_scores
from meshweave.scoring.ratings import aeo_rating, geo_rating
from webapp.db import get_session
from webapp.models import Crawl, ScoreSnapshot, User

logger = logging.getLogger(__name__)

# AAX queue configuration
AAX_STALE_MINUTES = 30  # Consider AAX "running" stale after this long
AAX_WORKER_POLL_INTERVAL = 5.0  # Seconds between queue polls


def score_crawl(
    crawl_id: str,
    *,
    payload: dict[str, Any] | None = None,
    manual_inputs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute AEO/GEO scores for a crawl and persist to DB.

    Args:
        crawl_id: The Crawl row ID.
        payload: Pre-loaded crawl payload dict. If None, loads from
            the Crawl row's payload_json. Pass this when you already
            have the payload in memory (e.g. after a crawl completes).
        manual_inputs: Optional manual score inputs for non-auto
            factors (capture_rate, query_match, voice_rate, citation).

    Returns:
        The full score_json dict from the scoring engine.

    Raises:
        ValueError: If the crawl row is not found.
    """
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            raise ValueError(f"Crawl {crawl_id} not found")

        # Load payload from DB if not provided
        if payload is None:
            payload = _payload_from_row(row)

    # Compute scores
    score_json = compute_scores(payload, manual_inputs=manual_inputs)
    aeo_composite = score_json.get("aeo", {}).get("composite")
    geo_composite = score_json.get("geo", {}).get("composite")
    aeo_r = aeo_rating(aeo_composite)
    geo_r = geo_rating(geo_composite)

    # Re-generate recommendations with AAX factors if AAX is in the payload
    if payload:
        _apply_aax_recommendations(score_json, payload)

    # Persist to DB
    _persist_scores(
        crawl_id,
        score_json,
        aeo_composite,
        geo_composite,
        aeo_r,
        geo_r,
        manual_inputs,
    )

    return score_json


def _payload_from_row(row: Crawl) -> dict[str, Any]:
    """Load the crawl payload from a row's payload_json column.

    Args:
        row: The Crawl row to read from.

    Returns:
        The parsed payload; an empty dict when missing or invalid.
    """
    raw = row.payload_json or {}
    if not isinstance(raw, str):
        return raw
    try:
        payload: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload


def _apply_aax_recommendations(
    score_json: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Re-generate recommendations with AAX factors when AAX completed.

    Args:
        score_json: The score_json dict to update in place.
        payload: The crawl payload possibly containing AAX results.
    """
    aax_result = payload.get("aax")
    if not aax_result or aax_result.get("status") != "completed":
        return
    aax_score_dict = compute_aax_score(aax_result)
    if not aax_score_dict:
        return
    from meshweave.scoring.recommendations import generate_recommendations

    aeo_factors = score_json.get("aeo", {}).get("factors", {})
    geo_factors = score_json.get("geo", {}).get("factors", {})
    all_recommendations = generate_recommendations(
        aeo_factors,
        geo_factors,
        payload=payload,
        aax_factors=aax_score_dict.get("factors"),
        contactability=aax_result.get("contactability"),
    )
    score_json["recommendations"] = all_recommendations


def _persist_scores(
    crawl_id: str,
    score_json: dict[str, Any],
    aeo_composite: Any,
    geo_composite: Any,
    aeo_r: str | None,
    geo_r: str | None,
    manual_inputs: dict[str, float] | None,
) -> None:
    """Persist computed scores to the Crawl row and ScoreSnapshot.

    Args:
        crawl_id: The Crawl row ID.
        score_json: The full score_json dict from the scoring engine.
        aeo_composite: The AEO composite score.
        geo_composite: The GEO composite score.
        aeo_r: The AEO rating label.
        geo_r: The GEO rating label.
        manual_inputs: Manual inputs passed to score_crawl, if any.

    Raises:
        ValueError: If the crawl row disappeared during scoring.
    """
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            raise ValueError(f"Crawl {crawl_id} disappeared during scoring")

        # Update Crawl score columns
        row.aeo_score = aeo_composite
        row.geo_score = geo_composite
        row.aeo_rating = aeo_r
        row.geo_rating = geo_r

        # Create or update ScoreSnapshot
        snap = row.score_snapshot
        if snap:
            snap.score_json = score_json
            snap.aeo_score = aeo_composite
            snap.geo_score = geo_composite
            snap.aeo_rating = aeo_r
            snap.geo_rating = geo_r
            snap.has_manual_input = bool(manual_inputs)
        else:
            snap = ScoreSnapshot(
                crawl_id=crawl_id,
                user_id=row.user_id,
                domain=row.domain or "",
                aeo_score=aeo_composite,
                geo_score=geo_composite,
                aeo_rating=aeo_r,
                geo_rating=geo_r,
                score_json=score_json,
                has_manual_input=bool(manual_inputs),
            )
            s.add(snap)


def enqueue_aax(crawl_id: str) -> bool:
    """Mark a crawl as needing AAX analysis. Durable — survives restarts.

    Called immediately after a crawl succeeds. Sets aax_status='pending'
    so the background worker will pick it up.

    Args:
        crawl_id: The Crawl row ID.

    Returns:
        True when the row was updated, False when the crawl was not found
        or AAX was already enqueued/completed.
    """
    with get_session() as s:
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.id == crawl_id,
                Crawl.status == "succeeded",
                Crawl.aax_status.in_(["pending", "failed"]),  # allow retry on failed
            )
            .update(
                {
                    "aax_status": "pending",
                    "aax_started_at": None,
                },
                synchronize_session=False,
            )
        )
        return updated == 1


async def run_aax_for_crawl(
    crawl_id: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run AAX analysis for a crawl and persist results.

    Called by the AAX worker after claiming a pending row. Runs LLM-powered
    tests asynchronously and stores results in ai_analysis_json on the
    ScoreSnapshot.

    Args:
        crawl_id: The Crawl row ID.
        payload: Pre-loaded crawl payload. If None, loads from DB.

    Returns:
        The AAX score dict, or None if AAX is disabled/ineligible.
    """
    from meshweave.ai.analyses import run_aax_analysis

    payload, user_id, user_email, anonymous_user_id = _load_payload_for_aax(
        crawl_id, payload
    )
    if payload is None:
        _mark_aax_terminal(crawl_id, "failed", "payload_missing")
        return None

    # Run AAX analysis
    try:
        aax_result = await run_aax_analysis(
            payload,
            trace_user_id=user_id,
            trace_user_email=user_email,
            trace_anonymous_user_id=anonymous_user_id,
            trace_session_id=f"aax:{crawl_id}",
        )
    except Exception as e:
        logger.warning("AAX analysis failed for crawl %s: %s", crawl_id, e)
        aax_result = {"status": "failed", "error": str(e)}

    if not aax_result:
        _mark_aax_terminal(crawl_id, "failed", "empty_result")
        return aax_result

    if aax_result.get("status") == "failed":
        # Persist the failure marker so the result page stops showing
        # "Running AI Analysis…" for a crawl whose AAX analysis died.
        _persist_aax_failure(crawl_id, aax_result)
        _mark_aax_terminal(crawl_id, "failed", aax_result.get("error"))
        return aax_result

    if aax_result.get("status") == "disabled":
        _mark_aax_terminal(crawl_id, "disabled", None)
        return aax_result

    # Compute AAX composite score
    aax_score_json = compute_aax_score(aax_result)

    # Persist to DB — ScoreSnapshot and payload_json
    _apply_aax_to_score_snapshot(crawl_id, aax_result, aax_score_json, payload)
    _inject_aax_into_payload_json(crawl_id, aax_result, aax_score_json)
    _mark_aax_terminal(crawl_id, "completed", None)

    return aax_score_json


def _mark_aax_terminal(crawl_id: str, status: str, error: str | None) -> None:
    """Mark AAX as terminally complete on the Crawl row.

    Args:
        crawl_id: The Crawl row ID.
        status: Terminal status: "completed" | "failed" | "disabled".
        error: Optional error message for failed status.
    """
    try:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if row:
                row.aax_status = status
                if error:
                    # Store AAX error in the crawl error field only if
                    # there isn't already a crawl error there.
                    if not row.error:
                        row.error = f"aax_{status}: {error}"
    except Exception:
        logger.exception("Failed to mark AAX %s for crawl %s", status, crawl_id)


def _persist_aax_failure(crawl_id: str, aax_result: dict[str, Any]) -> None:
    """Record a failed AAX analysis on the crawl's snapshot and payload.

    Best-effort: a persistence error must not mask the analysis
    failure itself.
    """
    try:
        with get_session() as s:
            snap = (
                s.query(ScoreSnapshot)
                .filter(ScoreSnapshot.crawl_id == crawl_id)
                .first()
            )
            if snap:
                existing = snap.ai_analysis_json or {}
                existing["aax"] = aax_result
                snap.ai_analysis_json = existing
                flag_modified(snap, "ai_analysis_json")
        _inject_aax_into_payload_json(crawl_id, aax_result, None)
    except Exception:
        logger.exception("Failed to persist AAX failure for crawl %s", crawl_id)


def _resolve_crawl_owner(
    row: Crawl | None,
) -> tuple[str | None, str | None, str | None]:
    """Extract owner identity from a Crawl row for LLM trace attribution.

    Args:
        row: The Crawl row, or None.

    Returns:
        Tuple of (user_id, user_email, anonymous_user_id).
    """
    if not row:
        return None, None, None
    user_id = row.user_id
    anonymous_user_id = row.anonymous_user_id if not user_id else None
    return user_id, None, anonymous_user_id


def _load_payload_from_row(row: Crawl) -> dict[str, Any]:
    """Parse payload_json from a Crawl row.

    Args:
        row: The Crawl row to read from.

    Returns:
        The parsed payload dict, or empty dict on failure.
    """
    raw = row.payload_json or {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw


def _load_payload_for_aax(
    crawl_id: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """Load the AAX payload and resolve the crawl owner.

    Args:
        crawl_id: The Crawl row ID.
        payload: Pre-loaded crawl payload. If None, loads from DB.

    Returns:
        The payload, owner ID, owner email, and anonymous browser ID. Returns
        ``(None, None, None, None)`` when the crawl row is missing and no
        payload was provided.
    """
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        user_id, user_email, anonymous_user_id = _resolve_crawl_owner(row)
        if user_id and not user_email:
            user = s.get(User, user_id)
            user_email = user.email if user else None
        if payload is None:
            if not row:
                return None, None, None, None
            payload = _load_payload_from_row(row)
    return payload, user_id, user_email, anonymous_user_id


def _apply_aax_to_score_snapshot(
    crawl_id: str,
    aax_result: dict[str, Any],
    aax_score_json: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> None:
    """Persist AAX results into the ScoreSnapshot on the crawl."""
    with get_session() as s:
        snap = s.query(ScoreSnapshot).filter(ScoreSnapshot.crawl_id == crawl_id).first()
        if not snap:
            return
        existing = snap.ai_analysis_json or {}
        existing["aax"] = aax_result
        snap.ai_analysis_json = existing
        flag_modified(snap, "ai_analysis_json")

        if aax_score_json and snap.score_json:
            snap.score_json["aax"] = aax_score_json

            # Re-generate recommendations now that AAX is available
            try:
                _regenerate_aax_recommendations(
                    snap, payload, aax_result, aax_score_json
                )
            except Exception:
                logger.debug(
                    "Failed to re-generate AAX recommendations",
                    exc_info=True,
                )

            flag_modified(snap, "score_json")


def _regenerate_aax_recommendations(
    snap: ScoreSnapshot,
    payload: dict[str, Any] | None,
    aax_result: dict[str, Any],
    aax_score_json: dict[str, Any],
) -> None:
    """Re-generate recommendations with AAX factors and store them."""
    from meshweave.scoring.engine import compute_scores
    from meshweave.scoring.recommendations import generate_recommendations

    base = compute_scores(payload or {})
    aeo_f = base.get("aeo", {}).get("factors", {})
    geo_f = base.get("geo", {}).get("factors", {})
    all_recs = generate_recommendations(
        aeo_f,
        geo_f,
        payload=payload,
        aax_factors=aax_score_json.get("factors"),
        contactability=aax_result.get("contactability"),
    )
    snap.score_json["recommendations"] = all_recs


def _parse_payload_dict(raw: Any) -> dict[str, Any]:
    """Coerce a raw payload value into a dict.

    Args:
        raw: The raw payload_json value (dict, str, or None).

    Returns:
        The payload as a dict, or empty dict on failure.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError, TypeError:
            return {}
    return {}


def _inject_aax_into_payload_json(
    crawl_id: str,
    aax_result: dict[str, Any],
    aax_score_json: dict[str, Any] | None,
) -> None:
    """Inject AAX into payload_json so the API returns it."""
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row or not row.payload_json:
            return
        p = _parse_payload_dict(row.payload_json)
        if not p:
            return
        p["aax"] = aax_result
        if aax_score_json:
            scores = p.get("scores") or {}
            scores["aax"] = aax_score_json
            p["scores"] = scores
        row.payload_json = p
        flag_modified(row, "payload_json")


def update_manual_inputs(crawl_id: str, inputs: dict[str, float]) -> dict[str, Any]:
    """Update manual score inputs and recompute.

    Args:
        crawl_id: The Crawl row ID.
        inputs: Dict of manual input values (capture_rate, query_match,
            voice_rate, citation). Values should be 0-100.

    Returns:
        The updated score_json dict.

    Raises:
        ValueError: If the crawl row or snapshot is not found.
    """
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            raise ValueError(f"Crawl {crawl_id} not found")
        if not row.score_snapshot:
            raise ValueError(f"Scores not computed yet for crawl {crawl_id}")

    return score_crawl(crawl_id, manual_inputs=inputs)


def get_score_history(domain: str, limit: int = 10) -> list[dict[str, Any]]:
    """Get score history for a domain.

    Args:
        domain: The domain to query.
        limit: Maximum number of snapshots to return.

    Returns:
        List of score summary dicts ordered by created_at DESC.
    """
    with get_session() as s:
        snapshots = (
            s.query(ScoreSnapshot)
            .filter(ScoreSnapshot.domain == domain)
            .order_by(ScoreSnapshot.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "crawl_id": snap.crawl_id,
                "aeo_score": snap.aeo_score,
                "geo_score": snap.geo_score,
                "aeo_rating": snap.aeo_rating,
                "geo_rating": snap.geo_rating,
                "scoring_version": snap.scoring_version,
                "has_manual_input": snap.has_manual_input,
                "created_at": snap.created_at.isoformat(),
            }
            for snap in snapshots
        ]


# ---------------------------------------------------------------------------
# AAX Queue Worker
# ---------------------------------------------------------------------------


def _claim_pending_aax(crawl_id: str) -> bool:
    """Atomically claim a pending AAX job for processing.

    Transitions aax_status from "pending" to "running". Uses an atomic
    UPDATE ... WHERE to prevent double-claiming by concurrent workers.

    Args:
        crawl_id: The Crawl row ID to claim.

    Returns:
        True when the row was successfully claimed, False otherwise.
    """
    now = datetime.now(UTC)
    with get_session() as s:
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.id == crawl_id,
                Crawl.aax_status == "pending",
            )
            .update(
                {
                    "aax_status": "running",
                    "aax_started_at": now,
                },
                synchronize_session=False,
            )
        )
        return updated == 1


def _reset_stale_aax() -> int:
    """Reset stale "running" AAX jobs back to "pending".

    A job is stale when it has been "running" for longer than
    AAX_STALE_MINUTES. This happens when the worker crashes or the
    container restarts mid-analysis.

    Returns:
        Number of rows reset.
    """
    stale_cutoff = datetime.now(UTC) - timedelta(minutes=AAX_STALE_MINUTES)
    with get_session() as s:
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.aax_status == "running",
                Crawl.aax_started_at < stale_cutoff,
            )
            .update(
                {
                    "aax_status": "pending",
                    "aax_started_at": None,
                },
                synchronize_session=False,
            )
        )
        if updated:
            logger.info("Reset %s stale AAX jobs to pending", updated)
        return updated


def _fetch_pending_aax_ids(limit: int = 10) -> list[str]:
    """Fetch IDs of crawls with pending AAX analysis.

    Args:
        limit: Maximum number of IDs to return.

    Returns:
        List of crawl IDs ready for AAX processing.
    """
    with get_session() as s:
        rows = (
            s.query(Crawl.id)
            .filter(
                Crawl.status == "succeeded",
                Crawl.aax_status == "pending",
            )
            .order_by(Crawl.updated_at.asc())
            .limit(limit)
            .all()
        )
        return [r[0] for r in rows]


async def _process_aax_job(crawl_id: str) -> None:
    """Claim and run a single AAX job, marking terminal state on failure.

    Args:
        crawl_id: The Crawl row ID to process.
    """
    if not _claim_pending_aax(crawl_id):
        return
    logger.debug("Claimed AAX job for crawl %s", crawl_id)
    try:
        await run_aax_for_crawl(crawl_id)
    except Exception:
        logger.exception("AAX worker failed for crawl %s", crawl_id)
        _mark_aax_terminal(crawl_id, "failed", "worker_exception")


async def _aax_worker_loop(stop_event: asyncio.Event) -> None:
    """Poll the AAX queue and process pending jobs.

    Args:
        stop_event: Event to signal graceful shutdown.
    """
    while not stop_event.is_set():
        try:
            pending_ids = _fetch_pending_aax_ids(limit=5)
            for crawl_id in pending_ids:
                if stop_event.is_set():
                    break
                await _process_aax_job(crawl_id)
        except Exception:
            logger.exception("AAX worker poll failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=AAX_WORKER_POLL_INTERVAL)
        except TimeoutError:
            pass


async def aax_worker(stop_event: asyncio.Event) -> None:
    """Background worker that processes the AAX queue.

    Polls the database for pending AAX jobs, claims them atomically,
    and runs the analysis. Designed to run as a long-lived asyncio task
    inside the FastAPI lifespan.

    Args:
        stop_event: Event to signal graceful shutdown.
    """
    logger.info("AAX worker started")
    try:
        _reset_stale_aax()
    except Exception:
        logger.exception("Failed to reset stale AAX jobs on startup")
    await _aax_worker_loop(stop_event)
    logger.info("AAX worker stopped")
