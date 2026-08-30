"""Tests for scoring engine, ratings, and recommendation grouping."""

from __future__ import annotations

from meshweave.scoring.engine import AAX_WEIGHTS, compute_aax_score
from meshweave.scoring.ratings import aax_rating
from meshweave.scoring.recommendations import (
    _content_delta_rec,
    _meta_issues,
)
from webapp.utils.scoring import group_recommendations_by_pillar, rating_class


class TestAAXCompositeFormula:
    """Verify AAX weighted composite produces correct results."""

    def test_perfect_scores(self):
        result = compute_aax_score(
            {
                "status": "completed",
                "homepage_comprehension": {
                    "brand": "Acme",
                    "product": "Widgets",
                    "target_audience": "Everyone",
                    "call_to_action": "Buy now",
                    "clarity": "clear",
                    "information_density": "dense",
                    "would_remember": True,
                    "key_features": ["A", "B", "C", "D"],
                },
                "meta_optimization": {
                    "brand": "Acme",
                    "product": "Widgets",
                    "target_audience": "Everyone",
                    "would_click_through": True,
                    "completeness": "complete",
                    "clarity": "clear",
                    "llm_optimization": "optimized",
                },
                "content_delta": {
                    "company": {"name": "Acme", "description": "Great"},
                    "product": {
                        "name": "Widgets",
                        "description": "Best",
                        "features": ["A", "B"],
                    },
                    "pricing": {"model": "subscription"},
                    "target_audience": "All",
                    "strengths": ["Fast"],
                    "coherence": "consistent",
                    "completeness": "comprehensive",
                },
                "llms_txt": {
                    "llms_txt": {"exists": True},
                    "llms_full_txt": {"exists": True},
                },
                "email_validation": {
                    "valid_contacts": [
                        {"email": "sales@acme.com", "contact_type": "sales"},
                    ],
                    "best_contact": "sales@acme.com",
                    "confidence": "high",
                },
                "contactability": {"score": 100.0, "email_count": 1},
                "tests_completed": 6,
                "tests_skipped": 0,
            }
        )
        # All factors scored → composite should be high
        assert result is not None
        assert result["composite"] is not None
        assert result["composite"] > 80

    def test_zero_scores(self):
        result = compute_aax_score(
            {
                "status": "completed",
                "contactability": {"score": 0.0, "email_count": 0},
                "tests_completed": 0,
                "tests_skipped": 5,
            }
        )
        # Contactability is always present; all-zero factors score 0
        assert result is not None
        assert result["composite"] == 0.0

    def test_disabled_status(self):
        result = compute_aax_score({"status": "disabled"})
        assert result is None

    def test_none_input(self):
        result = compute_aax_score(None)
        assert result is None

    def test_weight_sum_is_one(self):
        total = sum(AAX_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01


class TestMetaOptimizationRebalance:
    """Meta verdicts (no identity fields) carry the rebalanced sub-weights."""

    def _meta(self, **overrides) -> dict:
        base = {
            "would_click_through": True,
            "completeness": "complete",
            "clarity": "clear",
            "llm_optimization": "optimized",
        }
        base.update(overrides)
        return base

    def test_meta_still_scores_perfect_without_identity_fields(self):
        result = compute_aax_score(
            {
                "status": "completed",
                "homepage_comprehension": {
                    "clarity": "clear",
                    "information_density": "dense",
                    "would_remember": True,
                },
                # No brand/product/audience in meta at all
                "meta_optimization": self._meta(),
                "content_delta": {
                    "company": {"name": "Acme"},
                    "pricing": {"model": "subscription"},
                    "target_audience": "All",
                    "strengths": ["Fast"],
                    "coherence": "consistent",
                    "completeness": "comprehensive",
                },
                "llms_txt": {
                    "llms_txt": {"exists": True},
                    "llms_full_txt": {"exists": True},
                },
                "email_validation": {
                    "valid_contacts": [
                        {"email": "sales@acme.com", "contact_type": "sales"},
                    ],
                    "best_contact": "sales@acme.com",
                    "confidence": "high",
                },
                "contactability": {"score": 100.0, "email_count": 1},
                "tests_completed": 6,
                "tests_skipped": 0,
            }
        )
        assert result is not None
        assert result["factors"]["meta_optimization"]["score"] == 100.0


class TestAAXRatingThresholds:
    """Verify AAX rating threshold bands match the plan spec."""

    def test_opaque_range(self):
        assert aax_rating(0) == "Opaque"
        assert aax_rating(15) == "Opaque"
        assert aax_rating(24) == "Opaque"

    def test_unclear_range(self):
        assert aax_rating(25) == "Unclear"
        assert aax_rating(33) == "Unclear"
        assert aax_rating(39) == "Unclear"

    def test_readable_range(self):
        assert aax_rating(40) == "Readable"
        assert aax_rating(50) == "Readable"
        assert aax_rating(59) == "Readable"

    def test_clear_range(self):
        assert aax_rating(60) == "Clear"
        assert aax_rating(70) == "Clear"
        assert aax_rating(79) == "Clear"

    def test_fluent_range(self):
        assert aax_rating(80) == "Fluent"
        assert aax_rating(92) == "Fluent"
        assert aax_rating(100) == "Fluent"

    def test_none_score(self):
        assert aax_rating(None) is None

    def test_rating_class_mapping(self):
        assert rating_class("Opaque") == "rating-low"
        assert rating_class("Unclear") == "rating-low"
        assert rating_class("Readable") == "rating-ok"
        assert rating_class("Clear") == "rating-good"
        assert rating_class("Fluent") == "rating-excellent"


class TestMetaIssuesSurfacesSuggestions:
    """The meta recommendation should surface the LLM's improvement suggestions."""

    def test_uses_improvement_suggestions_when_present(self):
        raw = {
            "improvement_suggestions": [
                "Add a Twitter Card image",
                "Include a canonical URL",
            ],
            "would_click_through": True,
            "completeness": "complete",
            "clarity": "clear",
        }
        issues = _meta_issues(raw)
        assert issues == ["Add a Twitter Card image", "Include a canonical URL"]

    def test_suggestion_list_is_capped(self):
        raw = {"improvement_suggestions": ["a", "b", "c", "d", "e"]}
        assert len(_meta_issues(raw)) == 3

    def test_ignores_empty_and_whitespace_suggestions(self):
        raw = {"improvement_suggestions": ["", "   ", "Real fix"]}
        assert _meta_issues(raw) == ["Real fix"]

    def test_empty_whitespace_only_falls_back_to_structuring(self):
        raw = {
            "improvement_suggestions": ["  ", ""],
            "completeness": "minimal",
            "clarity": "unclear",
            "llm_optimization": "poor",
            "would_click_through": False,
        }
        issues = _meta_issues(raw)
        assert "value proposition" in issues
        assert "metadata" in issues

    def test_falls_back_to_structuring_when_no_suggestions(self):
        raw = {
            "completeness": "minimal",
            "clarity": "unclear",
            "llm_optimization": "poor",
            "would_click_through": False,
        }
        issues = _meta_issues(raw)
        assert "value proposition" in issues
        assert "metadata" in issues

    def test_empty_when_metadata_is_healthy_and_no_suggestions(self):
        raw = {
            "completeness": "complete",
            "clarity": "clear",
            "llm_optimization": "optimized",
            "would_click_through": True,
        }
        assert _meta_issues(raw) == []


class TestContentDeltaRec:
    """The weaknesses fix should always surface at high priority."""

    def test_emits_at_high_priority_regardless_of_strengths(self):
        factors = {
            "content_delta": {
                "score": 80,
                "raw": {
                    "weaknesses": ["No pricing found"],
                    "strengths": ["Fast load", "Good structure", "Clear nav"],
                },
            }
        }
        recs = _content_delta_rec(factors)
        assert len(recs) == 1
        assert recs[0]["priority"] == "high"
        assert "No pricing found" in recs[0]["detail"]

    def test_emits_even_with_many_strengths(self):
        factors = {
            "content_delta": {
                "score": 80,
                "raw": {
                    "weaknesses": ["Ambiguous pricing"],
                    "strengths": ["One", "Two", "Three", "Four"],
                },
            }
        }
        recs = _content_delta_rec(factors)
        assert len(recs) == 1
        assert recs[0]["priority"] == "high"

    def test_no_weakness_rec_when_none(self):
        factors = {"content_delta": {"raw": {"weaknesses": [], "strengths": ["Fast"]}}}
        recs = [
            r for r in _content_delta_rec(factors) if r["title"].startswith("Address")
        ]
        assert recs == []


class TestGroupRecommendationsByPillar:
    """Verify recommendation grouping by pillar."""

    def test_basic_grouping(self):
        recs = [
            {"factor": "schema", "pillar": "aeo", "priority": "high"},
            {"factor": "eeat", "pillar": "geo", "priority": "medium"},
            {"factor": "llms_txt", "pillar": "aax", "priority": "low"},
        ]
        groups = group_recommendations_by_pillar(recs)
        assert len(groups["aeo"]) == 1
        assert len(groups["geo"]) == 1
        assert len(groups["aax"]) == 1

    def test_priority_sorting(self):
        recs = [
            {"pillar": "aeo", "priority": "low"},
            {"pillar": "aeo", "priority": "high"},
            {"pillar": "aeo", "priority": "medium"},
        ]
        groups = group_recommendations_by_pillar(recs)
        priorities = [r["priority"] for r in groups["aeo"]]
        assert priorities == ["high", "medium", "low"]

    def test_empty_input(self):
        groups = group_recommendations_by_pillar([])
        assert groups["aeo"] == []
        assert groups["geo"] == []
        assert groups["aax"] == []

    def test_unknown_pillar_defaults_to_aeo(self):
        recs = [
            {"factor": "unknown_factor", "priority": "high"},
        ]
        groups = group_recommendations_by_pillar(recs)
        assert len(groups["aeo"]) == 1

    def test_multiple_pillars_mixed(self):
        recs = [
            {"pillar": "geo", "priority": "high"},
            {"pillar": "aeo", "priority": "medium"},
            {"pillar": "aax", "priority": "high"},
            {"pillar": "geo", "priority": "low"},
        ]
        groups = group_recommendations_by_pillar(recs)
        assert len(groups["geo"]) == 2
        assert groups["geo"][0]["priority"] == "high"
        assert groups["geo"][1]["priority"] == "low"
