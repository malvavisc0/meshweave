"""AEO/GEO score API router."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from meshweave.scoring.engine import compute_scores
from webapp.db import get_session
from webapp.models import Crawl, ScoreSnapshot
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
    valid_keys = {"capture_rate", "query_match", "voice_rate", "citation"}
    inputs: dict[str, float] = {}
    for key in valid_keys:
        if key in data and data[key] is not None:
            try:
                val = float(data[key])
                if 0 <= val <= 100:
                    inputs[key] = val
            except ValueError, TypeError:
                pass

    if not inputs:
        raise HTTPException(status_code=400, detail="No valid inputs provided")

    # Load existing score snapshot
    snapshot = row.score_snapshot
    if not snapshot:
        raise HTTPException(status_code=404, detail="Scores not computed yet")

    payload = row.payload_json or {}
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}

    # Recompute with manual inputs
    score_json = compute_scores(payload, manual_inputs=inputs)
    score_data = _build_score_data_for_template(score_json)

    # Update snapshot
    with get_session() as s:
        snap = s.get(ScoreSnapshot, snapshot.id)
        if snap:
            snap.score_json = score_json
            snap.aeo_score = score_json.get("aeo", {}).get("composite")
            snap.geo_score = score_json.get("geo", {}).get("composite")
            from meshweave.scoring.ratings import aeo_rating, geo_rating

            snap.aeo_rating = aeo_rating(snap.aeo_score)
            snap.geo_rating = geo_rating(snap.geo_score)
            snap.has_manual_input = True

    return JSONResponse(
        content={
            "crawl_id": crawl_id,
            "aeo_score": snapshot.aeo_score,
            "geo_score": snapshot.geo_score,
            "aeo_rating": snapshot.aeo_rating,
            "geo_rating": snapshot.geo_rating,
            "score_data": score_data,
            "manual_input_fields": _build_manual_input_fields(score_json),
            "has_manual_missing": _has_manual_missing(score_json),
        }
    )
