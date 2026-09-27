"""Actionable-factor inputs shared by the scorer and the fix generator."""

from __future__ import annotations

from typing import Any

# Categorical LLM verdict → numeric mappings for scoring
# Floor values lowered so "adequate" / "somewhat" responses don't
# inflate scores as much — creates realistic pressure.
CLARITY_MAP = {"clear": 100, "somewhat_clear": 50, "unclear": 15}
DENSITY_MAP = {"dense": 100, "adequate": 60, "sparse": 25, "bloated": 15}
COMPLETENESS_MAP = {"complete": 100, "partial": 50, "minimal": 15}
COHERENCE_MAP = {
    "consistent": 100,
    "somewhat_consistent": 50,
    "contradictory": 15,
}
CONTENT_COMPLETENESS_MAP = {
    "comprehensive": 100,
    "adequate": 50,
    "incomplete": 15,
}
LLM_OPT_MAP = {"optimized": 100, "adequate": 50, "poor": 15}
CONFIDENCE_MAP = {"high": 90, "medium": 55, "low": 25, "none": 5}

# Factor weights of the content-delta score.
RICHNESS_WEIGHT = 0.4
COHERENCE_WEIGHT = 0.3
COMPLETENESS_WEIGHT = 0.3

# Homepage identity fields and their share of homepage comprehension.
HOMEPAGE_FIELDS: tuple[str, ...] = (
    "brand",
    "product",
    "target_audience",
    "call_to_action",
)
HOMEPAGE_FIELD_WEIGHT = 0.4

# Email-validation presence: first valid contact, and the second's bonus.
FIRST_CONTACT_POINTS = 20.0
SECOND_CONTACT_POINTS = 10.0

# Best contact-type score awarded for a valid contact.
EMAIL_TYPE_SCORES: dict[str, int] = {
    "sales": 25,
    "support": 20,
    "general": 15,
    "legal": 5,
    "invalid": 0,
}


def richness_fields(cd: dict[str, Any]) -> dict[str, bool]:
    """Which buyer essentials the content-delta test could extract."""
    return {
        "company name": bool((cd.get("company") or {}).get("name")),
        "product name": bool((cd.get("product") or {}).get("name")),
        "pricing model": bool((cd.get("pricing") or {}).get("model")),
        "target audience": bool(cd.get("target_audience")),
        "strengths": bool(cd.get("strengths")),
    }


def richness_score(cd: dict[str, Any]) -> float:
    """Share of the buyer essentials the pages filled, on 0-100."""
    fields = richness_fields(cd)
    return sum(fields.values()) / len(fields) * 100
