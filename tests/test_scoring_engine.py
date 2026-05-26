"""Tests for scoring engine, ratings, and recommendation grouping."""

from __future__ import annotations

from meshweave.scoring.engine import AAX_WEIGHTS, compute_aax_score
from meshweave.scoring.ratings import aax_rating
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
                "tests_completed": 5,
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
                "tests_completed": 0,
                "tests_skipped": 5,
            }
        )
        # No factors → should return None
        assert result is None

    def test_disabled_status(self):
        result = compute_aax_score({"status": "disabled"})
        assert result is None

    def test_none_input(self):
        result = compute_aax_score(None)
        assert result is None

    def test_weight_sum_is_one(self):
        total = sum(AAX_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01


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
