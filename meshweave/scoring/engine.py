"""Scoring engine orchestrator.

Calls AEO and GEO factor scorers, computes composite scores,
generates recommendations, and returns the full score_json.
"""

from __future__ import annotations

import logging
from typing import Any

from meshweave.scoring import aeo as aeo_mod
from meshweave.scoring import geo as geo_mod
from meshweave.scoring.ratings import aax_rating, aeo_rating, geo_rating
from meshweave.scoring.recommendations import generate_recommendations

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


logger = logging.getLogger(__name__)


def _weighted_composite(
    factors: dict[str, dict],
    weights: dict[str, float],
) -> float | None:
    """Compute weighted composite, re-normalizing for available factors.

    Returns None if no factors have scores.
    """
    scored: dict[str, float] = {}
    for key, factor in factors.items():
        score = factor.get("score")
        if score is not None and key in weights:
            scored[key] = float(score)
        elif score is not None:
            logger.debug(
                "Factor %r has score %.1f but no weight — excluded",
                key,
                score,
            )

    if not scored:
        return None

    total_weight = sum(weights[k] for k in scored)
    if total_weight <= 0:
        return None

    composite = sum(scored[k] * weights[k] / total_weight for k in scored)

    # Calibration curve: compress the upper range so average sites don't
    # score artificially high. Power 1.15 maps 80→77, 70→66, 60→56,
    # 50→45, 40→35 while leaving 100 untouched. Compress more by raising
    # the exponent (e.g. 1.4 gives 80→73, 70→61, 60→49).
    calibrated = 100.0 * (float(composite) / 100.0) ** 1.15
    return float(round(min(100.0, calibrated), 1))


def compute_scores(
    payload: dict,
    manual_inputs: dict[str, float] | None = None,
    aax_factors: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Compute AEO and GEO scores from a crawl payload.

    Args:
        payload: The crawl result payload (JSON-parsed).
        manual_inputs: Optional dict of user-provided scores for
            non-auto-measurable factors. Keys match factor names
            (capture_rate, query_match, voice_rate, citation).
        aax_factors: Optional AAX factor dicts for generating AAX
            recommendations.

    Returns:
        Full score_json dict.
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
        aeo_factors, geo_factors, payload=payload, aax_factors=aax_factors
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


# AAX factor weights (excluding contactability — it's a separate signal)
AAX_WEIGHTS: dict[str, float] = {
    "homepage_comprehension": 0.30,
    "meta_optimization": 0.20,
    "content_delta": 0.20,
    "llms_txt": 0.15,
    "email_validation": 0.15,
}


def compute_aax_score(aax_result: dict[str, Any]) -> dict[str, Any] | None:
    """Compute AAX composite score from AAX analysis results.

    Takes the output of run_aax_analysis() and produces a score_json
    section for AAX.

    Returns None if AAX is disabled or has no completed tests.
    """
    if not _aax_completed(aax_result):
        return None

    # Score each test using categorical mappings
    factors: dict[str, dict] = {}
    _add_homepage_comprehension_factor(factors, aax_result)
    _add_meta_optimization_factor(factors, aax_result)
    _add_content_delta_factor(factors, aax_result)
    _add_llms_txt_factor(factors, aax_result)
    _add_email_validation_factor(factors, aax_result)

    if not factors:
        return None

    composite = _weighted_composite(factors, AAX_WEIGHTS)

    return {
        "composite": composite,
        "rating": aax_rating(composite),
        "factors": factors,
        "contactability": aax_result.get("contactability"),
        "skip_reasons": aax_result.get("skip_reasons", {}),
        "tests_completed": aax_result.get("tests_completed", 0),
        "tests_skipped": aax_result.get("tests_skipped", 0),
        "model_id": aax_result.get("model_id", ""),
    }


def _aax_completed(aax_result: dict[str, Any]) -> bool:
    """True when the AAX analysis ran to completion."""
    return bool(aax_result) and aax_result.get("status") == "completed"


def _add_homepage_comprehension_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the homepage-comprehension test into *factors*."""
    hc = aax_result.get("homepage_comprehension")
    if not hc:
        return
    from meshweave.ai.runner import CLARITY_MAP, DENSITY_MAP

    clarity = CLARITY_MAP.get(hc.get("clarity", "unclear"), 20)
    density = DENSITY_MAP.get(hc.get("information_density", "sparse"), 30)
    remember = 100 if hc.get("would_remember") else 0
    fields_filled = sum(
        1
        for k in ("brand", "product", "target_audience", "call_to_action")
        if hc.get(k)
    )
    field_score = (fields_filled / 4) * 100
    features_score = min(len(hc.get("key_features", [])) * 15, 60)
    factors["homepage_comprehension"] = {
        "score": min(
            100.0,
            float(
                field_score * 0.4
                + clarity * 0.2
                + density * 0.2
                + features_score * 0.1
                + remember * 0.1
            ),
        ),
        "weight": AAX_WEIGHTS["homepage_comprehension"],
        "auto_measurable": True,
        "raw": hc,
    }


def _add_meta_optimization_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the meta-optimization test into *factors*."""
    mo = aax_result.get("meta_optimization")
    if not mo:
        return
    from meshweave.ai.runner import CLARITY_MAP, COMPLETENESS_MAP, LLM_OPT_MAP

    completeness = COMPLETENESS_MAP.get(mo.get("completeness", "minimal"), 20)
    clarity = CLARITY_MAP.get(mo.get("clarity", "unclear"), 20)
    llm_opt = LLM_OPT_MAP.get(mo.get("llm_optimization", "poor"), 20)
    click = 100 if mo.get("would_click_through") else 0
    fields_filled = sum(1 for k in ("brand", "product", "target_audience") if mo.get(k))
    field_score = (fields_filled / 3) * 100
    factors["meta_optimization"] = {
        "score": min(
            100.0,
            float(
                field_score * 0.4
                + completeness * 0.2
                + clarity * 0.15
                + llm_opt * 0.15
                + click * 0.1
            ),
        ),
        "weight": AAX_WEIGHTS["meta_optimization"],
        "auto_measurable": True,
        "raw": mo,
    }


def _add_content_delta_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the content-delta test into *factors*."""
    cd = aax_result.get("content_delta")
    if not cd:
        return
    from meshweave.ai.runner import COHERENCE_MAP, CONTENT_COMPLETENESS_MAP

    coherence = COHERENCE_MAP.get(cd.get("coherence", "somewhat_consistent"), 60)
    completeness = CONTENT_COMPLETENESS_MAP.get(
        cd.get("completeness", "incomplete"), 20
    )
    # Info richness: how many fields were extracted
    richness_score = _content_richness_score(cd)
    factors["content_delta"] = {
        "score": min(
            100.0,
            float(richness_score * 0.4 + coherence * 0.3 + completeness * 0.3),
        ),
        "weight": AAX_WEIGHTS["content_delta"],
        "auto_measurable": True,
        "raw": cd,
    }


def _content_richness_score(cd: dict[str, Any]) -> float:
    """Score for how many extractable content fields the pages filled."""
    product = cd.get("product") or {}
    pricing = cd.get("pricing") or {}
    richness = sum(
        [
            bool((cd.get("company") or {}).get("name")),
            bool(product.get("name")),
            bool(product.get("description")),
            bool(product.get("features")),
            bool(pricing.get("model")),
            bool(cd.get("target_audience")),
            bool(cd.get("strengths")),
        ]
    )
    return (richness / 7) * 100


def _add_llms_txt_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the llms.txt presence heuristic into *factors*."""
    llms = aax_result.get("llms_txt")
    if not llms:
        return
    factors["llms_txt"] = {
        "score": _llms_txt_score(llms),
        "weight": AAX_WEIGHTS["llms_txt"],
        "auto_measurable": True,
        "raw": llms,
    }


def _llms_txt_score(llms: dict[str, Any]) -> float:
    """100 when both llms.txt variants exist, 60 for one, else 0."""
    llms_txt_data = llms.get("llms_txt") or {}
    llms_full_data = llms.get("llms_full_txt") or {}
    has_llms = llms_txt_data.get("exists", False)
    has_llms_full = llms_full_data.get("exists", False)
    if has_llms and has_llms_full:
        return 100.0
    if has_llms or has_llms_full:
        return 60.0
    return 0.0


def _add_email_validation_factor(
    factors: dict[str, dict],
    aax_result: dict[str, Any],
) -> None:
    """Score the email-validation test into *factors*."""
    ev = aax_result.get("email_validation")
    if not ev:
        return
    from meshweave.ai.runner import CONFIDENCE_MAP

    confidence = CONFIDENCE_MAP.get(ev.get("confidence", "low"), 30)
    contacts = ev.get("valid_contacts") or []
    presence = _email_presence_points(contacts)
    best_type = _email_type_points(contacts)
    has_best = 10 if ev.get("best_contact") else 0
    factors["email_validation"] = {
        "score": min(
            100.0,
            float(presence + best_type + confidence * 0.35 + has_best),
        ),
        "weight": AAX_WEIGHTS["email_validation"],
        "auto_measurable": True,
        "raw": ev,
    }


def _email_presence_points(contacts: list) -> float:
    """Presence points that saturate after the second valid contact."""
    if not contacts:
        return 0.0
    # Presence saturates quickly: one contact earns most of the
    # presence points, a second adds a little, more add nothing —
    # quantity must not outweigh quality.
    return min(30.0, 20.0 + 10.0 * min(len(contacts) - 1, 1))


# Best contact-type score awarded for a valid contact
_EMAIL_TYPE_SCORES: dict[str, int] = {
    "sales": 25,
    "support": 20,
    "general": 15,
    "legal": 5,
    "invalid": 0,
}


def _email_type_points(contacts: list) -> int:
    """Best contact-type score among the valid contacts."""
    return max(
        (_EMAIL_TYPE_SCORES.get(c.get("contact_type", "invalid"), 0) for c in contacts),
        default=0,
    )
