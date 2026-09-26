"""AEO/GEO score API router."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from meshweave.scoring.interpretation import interpret_profile
from webapp.db import get_session
from webapp.models import Crawl
from webapp.utils.scoring import (
    build_score_data_for_template as _build_score_data_for_template,
)

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
    )
    return JSONResponse(
        content={
            "crawl_id": crawl_id,
            "aeo_score": snapshot.aeo_score,
            "geo_score": snapshot.geo_score,
            "aeo_rating": snapshot.aeo_rating,
            "geo_rating": snapshot.geo_rating,
            "score_data": _build_score_data_for_template(score_data),
            "interpretation": interp,
        }
    )


@router.get("/api/scores/domain/{domain}")
async def get_domain_scores(domain: str, limit: int = 10):
    """Score history for a domain."""
    from webapp.services.scoring import get_score_history

    history = get_score_history(domain, limit=min(limit, 50))
    return JSONResponse(content={"domain": domain, "history": history})
