"""Centralized AEO/GEO/AAX scoring service.

Wraps the scoring engine and ScoreSnapshot persistence into reusable
functions, eliminating duplication across crawling.py, site_crawling.py,
and scores.py router.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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
            raw = row.payload_json or {}
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
            else:
                payload = raw

    # Compute scores
    score_json = compute_scores(payload, manual_inputs=manual_inputs)
    aeo_composite = score_json.get("aeo", {}).get("composite")
    geo_composite = score_json.get("geo", {}).get("composite")
    aeo_r = aeo_rating(aeo_composite)
    geo_r = geo_rating(geo_composite)

    # Persist to DB
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

    return score_json


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

    # Load payload if not provided
    if payload is None:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return None
            raw = row.payload_json or {}
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
            else:
                payload = raw

    # Run AAX analysis
    try:
        aax_result = await run_aax_analysis(payload)
    except Exception as e:
        logger.warning("AAX analysis failed for crawl %s: %s", crawl_id, e)
        aax_result = {"status": "failed", "error": str(e)}

    if not aax_result or aax_result.get("status") in ("disabled", "failed"):
        return aax_result

    # Compute AAX composite score
    aax_score_json = compute_aax_score(aax_result)

    # Persist to DB
    with get_session() as s:
        snap = s.query(ScoreSnapshot).filter(ScoreSnapshot.crawl_id == crawl_id).first()
        if snap:
            # Merge AAX into existing ai_analysis_json
            existing = snap.ai_analysis_json or {}
            existing["aax"] = aax_result
            snap.ai_analysis_json = existing

            # Store AAX composite in score_json
            if aax_score_json and snap.score_json:
                snap.score_json["aax"] = aax_score_json

    return aax_score_json


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
