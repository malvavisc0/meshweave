"""Centralized AEO/GEO/AAX scoring service.

Wraps the scoring engine and ScoreSnapshot persistence into reusable
functions, eliminating duplication across crawling.py, site_crawling.py,
and scores.py router.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from meshweave.scoring.engine import compute_aax_score, compute_scores
from meshweave.scoring.ratings import aeo_rating, geo_rating
from webapp.db import get_session
from webapp.models import Crawl, ScoreSnapshot

logger = logging.getLogger(__name__)


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


async def run_aax_for_crawl(
    crawl_id: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run AAX analysis for a crawl and persist results.

    Called after score_crawl() completes. Runs LLM-powered tests
    asynchronously and stores results in ai_analysis_json on the
    ScoreSnapshot.

    Args:
        crawl_id: The Crawl row ID.
        payload: Pre-loaded crawl payload. If None, loads from DB.

    Returns:
        The AAX score dict, or None if AAX is disabled/ineligible.
    """
    from meshweave.ai.analyses import run_aax_analysis

    payload, user_id = _load_payload_for_aax(crawl_id, payload)
    if payload is None:
        return None

    # Run AAX analysis
    try:
        aax_result = await run_aax_analysis(
            payload,
            trace_user_id=user_id,
            trace_session_id=f"aax:{crawl_id}",
        )
    except Exception as e:
        logger.warning("AAX analysis failed for crawl %s: %s", crawl_id, e)
        aax_result = {"status": "failed", "error": str(e)}

    if not aax_result or aax_result.get("status") in ("disabled", "failed"):
        return aax_result

    # Compute AAX composite score
    aax_score_json = compute_aax_score(aax_result)

    # Persist to DB — ScoreSnapshot and payload_json
    _apply_aax_to_score_snapshot(crawl_id, aax_result, aax_score_json, payload)
    _inject_aax_into_payload_json(crawl_id, aax_result, aax_score_json)

    return aax_score_json


def _load_payload_for_aax(
    crawl_id: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Load the AAX payload and resolve the crawl owner.

    Args:
        crawl_id: The Crawl row ID.
        payload: Pre-loaded crawl payload. If None, loads from DB.

    Returns:
        The (payload, user_id) pair. Returns (None, None) when the crawl
        row is missing and no payload was provided.
    """
    # Load payload if not provided; also resolve the crawl's owner so LLM
    # traces can be attributed to the user who requested the analysis.
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        user_id = row.user_id if row else None
        if payload is None:
            if not row:
                return None, None
            raw = row.payload_json or {}
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
            else:
                payload = raw
    return payload, user_id


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
        try:
            p = (
                row.payload_json or {}
                if isinstance(row.payload_json, str)
                else row.payload_json
            )
            if isinstance(p, dict):
                p["aax"] = aax_result
                if aax_score_json:
                    scores = p.get("scores") or {}
                    scores["aax"] = aax_score_json
                    p["scores"] = scores
                row.payload_json = p
                flag_modified(row, "payload_json")
        except json.JSONDecodeError:
            pass
        except TypeError:
            pass


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
