"""Scoring engine orchestrator.

Calls AEO and GEO factor scorers, computes composite scores,
generates recommendations, and returns the full score_json.
"""

from __future__ import annotations

from typing import Any

from meshweave.scoring import aeo as aeo_mod
from meshweave.scoring import geo as geo_mod
from meshweave.scoring.ratings import aeo_rating, geo_rating
from meshweave.scoring.recommendations import generate_recommendations

# Weight definitions per spec §4.2
AEO_WEIGHTS: dict[str, float] = {
    "capture_rate": 0.30,
    "schema": 0.20,
    "content_structure": 0.20,
    "query_match": 0.15,
    "voice_rate": 0.10,
    "freshness": 0.05,
}

GEO_WEIGHTS: dict[str, float] = {
    "citation": 0.30,
    "topical_authority": 0.20,
    "eeat": 0.15,
    "crawl_access": 0.15,
    "content_depth": 0.10,
    "entity_consistency": 0.10,
}


def _weighted_composite(
    factors: dict[str, dict],
    weights: dict[str, float],
) -> float | None:
    """Compute weighted composite, re-normalizing for available factors.

    Returns None if no factors have scores.
    """
    scored = {}
    for key, factor in factors.items():
        score = factor.get("score")
        if score is not None and key in weights:
            scored[key] = float(score)

    if not scored:
        return None

    total_weight = sum(weights[k] for k in scored)
    if total_weight <= 0:
        return None

    composite = sum(scored[k] * weights[k] / total_weight for k in scored)
    return round(min(100.0, composite), 1)


def compute_scores(
    payload: dict,
    manual_inputs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute AEO and GEO scores from a crawl payload.

    Args:
        payload: The crawl result payload (JSON-parsed).
        manual_inputs: Optional dict of user-provided scores for
            non-auto-measurable factors. Keys match factor names
            (capture_rate, query_match, voice_rate, citation).

    Returns:
        Full score_json dict matching spec §6.4.
    """
    manual = manual_inputs or {}

    # --- AEO factors ---
    aeo_factors: dict[str, dict] = {
        "schema": aeo_mod.score_schema(payload),
        "content_structure": aeo_mod.score_content_structure(payload),
        "freshness": aeo_mod.score_freshness(payload),
        "capture_rate": aeo_mod.score_capture_rate(manual.get("capture_rate")),
        "query_match": aeo_mod.score_query_match(manual.get("query_match")),
        "voice_rate": aeo_mod.score_voice_rate(manual.get("voice_rate")),
    }

    # --- GEO factors ---
    geo_factors: dict[str, dict] = {
        "topical_authority": geo_mod.score_topical_authority(payload),
        "eeat": geo_mod.score_eeat(payload),
        "crawl_access": geo_mod.score_crawl_access(payload),
        "content_depth": geo_mod.score_content_depth(payload),
        "entity_consistency": geo_mod.score_entity_consistency(payload),
        "citation": geo_mod.score_citation(manual.get("citation")),
    }

    # --- Composite scores ---
    aeo_composite = _weighted_composite(aeo_factors, AEO_WEIGHTS)
    geo_composite = _weighted_composite(geo_factors, GEO_WEIGHTS)

    # Auto-only composites (exclude manual-input factors)
    aeo_auto_only = {k: v for k, v in aeo_factors.items() if v.get("auto_measurable")}
    geo_auto_only = {k: v for k, v in geo_factors.items() if v.get("auto_measurable")}
    aeo_auto_composite = _weighted_composite(aeo_auto_only, AEO_WEIGHTS)
    geo_auto_composite = _weighted_composite(geo_auto_only, GEO_WEIGHTS)

    # --- Recommendations ---
    recommendations = generate_recommendations(
        aeo_factors, geo_factors, payload=payload
    )

    # --- Build score_json ---
    return {
        "aeo": {
            "composite": aeo_composite,
            "auto_only_composite": aeo_auto_composite,
            "rating": aeo_rating(aeo_composite),
            "auto_rating": aeo_rating(aeo_auto_composite),
            "factors": aeo_factors,
        },
        "geo": {
            "composite": geo_composite,
            "auto_only_composite": geo_auto_composite,
            "rating": geo_rating(geo_composite),
            "auto_rating": geo_rating(geo_auto_composite),
            "factors": geo_factors,
        },
        "recommendations": recommendations,
    }
