"""AEO/GEO score API router."""

import contextlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from meshweave.scoring.interpretation import interpret_profile
from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.auth import require_auth, require_ownership
from webapp.utils.scoring import build_manual_input_fields as _build_manual_input_fields
from webapp.utils.scoring import (
    build_score_data_for_template as _build_score_data_for_template,
)
from webapp.utils.scoring import has_manual_missing as _has_manual_missing

router = APIRouter()


@router.get("/api/scores/{crawl_id}")
async def get_scores(request: Request, crawl_id: str):
    """Retrieve score snapshot for a crawl."""
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    # Visibility check
    current_user = getattr(request.state, "current_user", None)
    is_owner = bool(current_user and getattr(row, "user_id", None) == current_user.id)
    if row.visibility == "private" and not is_owner:
        raise HTTPException(status_code=404, detail="Not found")

    snapshot = row.score_snapshot
    if not snapshot:
        return JSONResponse(
            status_code=404, content={"detail": "Scores not computed yet"}
        )

    score_data = snapshot.score_json or {}
    aax_section = score_data.get("aax", {})
    interp = interpret_profile(
        snapshot.aeo_score,
        snapshot.geo_score,
        aax_section.get("composite"),
        score_basis="full" if snapshot.has_manual_input else "auto",
    )
    return JSONResponse(
        content={
            "crawl_id": crawl_id,
            "aeo_score": snapshot.aeo_score,
            "geo_score": snapshot.geo_score,
            "aeo_rating": snapshot.aeo_rating,
            "geo_rating": snapshot.geo_rating,
            "score_data": _build_score_data_for_template(score_data),
            "manual_input_fields": _build_manual_input_fields(score_data),
            "has_manual_missing": _has_manual_missing(score_data),
            "interpretation": interp,
        }
    )


def _validated_manual_inputs(data: dict) -> dict[str, float]:
    """Extract valid manual score inputs from the request body.

    Args:
        data: Parsed JSON body.

    Returns:
        dict: Mapping of valid input keys to float values in [0, 100].
    """
    valid_keys = {"capture_rate", "query_match", "voice_rate", "citation"}
    inputs: dict[str, float] = {}
    for key in valid_keys:
        if key in data and data[key] is not None:
            with contextlib.suppress(ValueError, TypeError):
                val = float(data[key])
                if 0 <= val <= 100:
                    inputs[key] = val
    return inputs


def _recomputed_scores_response(
    crawl_id: str, row: Crawl, score_json: dict
) -> JSONResponse:
    """Build the recomputed-scores JSON response.

    Args:
        crawl_id: Crawl identifier.
        row: Crawl row carrying the score snapshot.
        score_json: Recomputed score JSON payload.

    Returns:
        JSONResponse: Response with recomputed scores and metadata.
    """
    score_data = _build_score_data_for_template(score_json)
    return JSONResponse(
        content={
            "crawl_id": crawl_id,
            "aeo_score": score_json.get("aeo", {}).get("composite"),
            "geo_score": score_json.get("geo", {}).get("composite"),
            "aeo_rating": row.score_snapshot.aeo_rating if row.score_snapshot else None,
            "geo_rating": row.score_snapshot.geo_rating if row.score_snapshot else None,
            "score_data": score_data,
            "manual_input_fields": _build_manual_input_fields(score_json),
            "has_manual_missing": _has_manual_missing(score_json),
        }
    )


@router.post("/api/scores/{crawl_id}/inputs")
async def update_manual_inputs(request: Request, crawl_id: str):
    """Accept manual score inputs and recompute scores."""
    await require_auth(request)
    row = await require_ownership(request, crawl_id)

    try:
        data = await request.json()
    except Exception:
        data = {}

    # Validate inputs
    inputs = _validated_manual_inputs(data)

    if not inputs:
        raise HTTPException(status_code=400, detail="No valid inputs provided")

    if not row.score_snapshot:
        raise HTTPException(status_code=404, detail="Scores not computed yet")

    # Recompute via scoring service
    from webapp.services.scoring import update_manual_inputs as _update

    try:
        score_json = _update(crawl_id, inputs)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return _recomputed_scores_response(crawl_id, row, score_json)


@router.get("/api/scores/domain/{domain}")
async def get_domain_scores(domain: str, limit: int = 10):
    """Score history for a domain."""
    from webapp.services.scoring import get_score_history

    history = get_score_history(domain, limit=min(limit, 50))
    return JSONResponse(content={"domain": domain, "history": history})
