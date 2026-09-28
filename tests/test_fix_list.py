"""Penalty-driven fixes, missing-fact fixes, and the prompts behind them."""

from __future__ import annotations

from meshweave.ai.analyses import _contactability_penalties
from meshweave.ai.models import (
    LEGAL_ONLY_EMAIL_PENALTY,
    OBFUSCATED_EMAIL_PENALTY,
    SAME_DOMAIN_EMAIL_PENALTY,
)
from meshweave.ai.prompts import aax_summary_prompt, content_delta_prompt
from meshweave.scoring.composite import expected_lens_delta
from meshweave.scoring.recommendations import generate_recommendations

_ALL_SIGNALS = {
    "has_email": True,
    "has_mailto": True,
    "has_contact_page": True,
    "has_contact_point_schema": True,
    "has_social_links": True,
}


def _penalties(pts: int, **overrides: object) -> tuple[int, dict[str, float]]:
    kwargs: dict = {
        "unique_emails": ["hello@other.example"],
        "has_mailto": True,
        "by_url": {"https://a/contact": ["hello@other.example"]},
        "homepage_emails": [],
        "contact_emails": ["hello@other.example"],
        "same_domain_emails": [],
    }
    kwargs.update(overrides)
    return _contactability_penalties(pts, **kwargs)


class TestPenaltyPoints:
    """Each penalty records the points it removed from the score."""

    def test_same_domain_cap_records_lost_points(self):
        pts, lost = _penalties(80)
        assert pts == 20
        assert lost == {SAME_DOMAIN_EMAIL_PENALTY: 60.0}

    def test_cap_below_threshold_costs_nothing(self):
        pts, lost = _penalties(15)
        assert pts == 15
        assert lost == {SAME_DOMAIN_EMAIL_PENALTY: 0.0}

    def test_subtractive_penalty_then_cap(self):
        pts, lost = _penalties(80, has_mailto=False)
        assert pts == 20
        assert lost == {OBFUSCATED_EMAIL_PENALTY: 10.0, SAME_DOMAIN_EMAIL_PENALTY: 50.0}

    def test_penalty_records_only_points_actually_removed(self):
        # 5 points, then −10 (floored at 0), then −15 (nothing left).
        pts, lost = _penalties(
            5,
            has_mailto=False,
            by_url={"https://a/privacy": ["x@y.z"]},
            contact_emails=[],
        )
        assert pts == 0
        assert lost == {
            OBFUSCATED_EMAIL_PENALTY: 5.0,
            LEGAL_ONLY_EMAIL_PENALTY: 0.0,
            SAME_DOMAIN_EMAIL_PENALTY: 0.0,
        }


def _aax_factors(contact_score: float) -> dict[str, dict]:
    return {
        "homepage_comprehension": {"score": 96.0, "raw": {}},
        "contactability": {"score": contact_score, "raw": {}},
    }


class TestPenaltyFixes:
    """A binding penalty yields a fix naming it and its predicted points."""

    def test_same_domain_cap_fix(self):
        contact = {
            **_ALL_SIGNALS,
            "score": 20.0,
            "penalties": [SAME_DOMAIN_EMAIL_PENALTY],
            "penalty_points": {SAME_DOMAIN_EMAIL_PENALTY: 60.0},
        }
        factors = _aax_factors(20.0)
        recs = generate_recommendations(
            {}, {}, aax_factors=factors, contactability=contact
        )
        fix = next(r for r in recs if r["factor"] == "contactability")
        assert fix["title"] == "Publish a contact email on your own domain"
        assert "capped at 20" in fix["detail"]
        assert "removes 60 points" in fix["detail"]
        # Lifting the cap restores 60 points; a same-domain address also
        # earns 15 more than the third-party one (20 vs 5).
        expected = expected_lens_delta("aax", factors, "contactability", 95.0)
        assert fix["expected_points"] == expected
        assert fix["expected_points"] > 0

    def test_signal_fix_respects_binding_cap(self):
        contact = {
            **_ALL_SIGNALS,
            "has_social_links": False,
            "score": 20.0,
            "penalty_points": {SAME_DOMAIN_EMAIL_PENALTY: 40.0},
        }
        recs = generate_recommendations(
            {}, {}, aax_factors=_aax_factors(20.0), contactability=contact
        )
        titles = [r["title"] for r in recs]
        # Adding social links under a binding cap moves nothing: dropped.
        assert "Improve contactability for AI agents" not in titles
        assert "Publish a contact email on your own domain" in titles

    def test_zero_cost_penalty_still_fixes_email_signal(self):
        contact = {
            **_ALL_SIGNALS,
            "has_email": False,
            "score": 10.0,
            "penalty_points": {SAME_DOMAIN_EMAIL_PENALTY: 0.0},
        }
        recs = generate_recommendations(
            {}, {}, aax_factors=_aax_factors(10.0), contactability=contact
        )
        fix = next(r for r in recs if r["factor"] == "contactability")
        assert fix["title"] == "Publish a contact email on your own domain"
        assert fix["expected_points"] > 0


class TestCrawlAccessFixes:
    """Crawl-access fixes predict on the rescaled 0-100 factor."""

    def test_llms_fix_target_is_rescaled(self):
        geo_factors = {
            "crawl_access": {
                "score": round(62 * 100 / 77, 1),
                "raw": {
                    "robots_exists": True,
                    "llms_txt_exists": False,
                    "llms_full_txt_exists": True,
                    "sitemap_count": 1,
                    "bot_statuses": {
                        "GPTBot": "allowed",
                        "ClaudeBot": "allowed",
                        "PerplexityBot": "allowed",
                    },
                },
            }
        }
        recs = generate_recommendations({}, geo_factors)
        assert [r["title"] for r in recs] == ["Publish an llms.txt file"]
        expected = expected_lens_delta("geo", geo_factors, "crawl_access", 100.0)
        assert recs[0]["expected_points"] == expected


class TestContentDeltaPrompt:
    """Weaknesses are missing buyer facts, never stated limitations."""

    def test_excludes_disclaimers_and_boundaries(self):
        user, _ = content_delta_prompt("page text")
        assert "ONLY facts a buyer needs" in user
        for excluded in ("limitations", "disclaimers", "product boundaries"):
            assert excluded in user
        assert "never list them as weaknesses" in user


class TestSummaryPrompt:
    """The summary reuses the site's own audience wording."""

    def test_reuses_stated_audience(self):
        user, _ = aax_summary_prompt({"target_audience": "agencies"}, None)
        assert "Name the audience exactly as the site states it" in user
        assert "never generalise" in user


class TestFreshnessHonesty:
    """Freshness findings match the evidence and never over-promise."""

    @staticmethod
    def _freshness(avg_days: float | None, score: float | None) -> dict[str, dict]:
        return {
            "freshness": {
                "score": score,
                "raw": {"avg_days_old": avg_days, "pages_with_dates": 2},
            }
        }

    def test_stale_dated_rec_fires_with_a_real_move_and_honest_copy(self):
        factors = self._freshness(150.0, 60.0)
        recs = generate_recommendations(factors, {})
        fix = next(r for r in recs if r["factor"] == "freshness")
        assert fix["expected_points"] is not None and fix["expected_points"] > 0
        assert "150 days" in fix["detail"]
        assert "carry no date" not in fix["detail"]
        assert "an agent can read" not in fix["detail"]
        assert "note" not in factors["freshness"]

    def test_dateless_freshness_yields_a_note_not_a_fix(self):
        factors = self._freshness(None, None)
        recs = generate_recommendations(factors, {})
        assert not [r for r in recs if r["factor"] == "freshness"]
        assert "No readable dates" in factors["freshness"]["note"]

    def test_mid_band_freshness_yields_a_note_not_a_fix(self):
        factors = self._freshness(50.0, 80.0)
        recs = generate_recommendations(factors, {})
        assert not [r for r in recs if r["factor"] == "freshness"]
        assert "50 days" in factors["freshness"]["note"]

    def test_zero_point_freshness_produces_no_fix_list_item(self):
        # Content older than a year: re-dating moves nothing. Predict 0,
        # drop the fix, and give the reason as a factor note.
        factors = self._freshness(400.0, 20.0)
        recs = generate_recommendations(factors, {})
        assert not [r for r in recs if r["factor"] == "freshness"]
        assert "older than a year" in factors["freshness"]["note"]

    def test_target_band_already_reached_predicts_no_move(self):
        # A predicted move of zero never reaches the fix list.
        factors = self._freshness(120.0, 80.0)
        recs = generate_recommendations(factors, {})
        assert not [r for r in recs if r["factor"] == "freshness"]
        assert factors["freshness"]["note"]


class TestGapNotes:
    """Sub-100 factors without a fix carry a reason note, never silence."""

    def test_mid_band_topical_authority_gets_a_note(self):
        raw = {
            "coverage_pct": 80,
            "schema_types_count": 1,
            "name_consistent": True,
            "desc_consistent": True,
            "content_page_ratio": 0.5,
        }
        factors = {"topical_authority": {"score": 60.6, "raw": raw}}
        recs = generate_recommendations({}, factors)
        assert not [r for r in recs if r["factor"] == "topical_authority"]
        assert factors["topical_authority"]["note"]

    def test_mid_band_content_depth_gets_a_note(self):
        factors = {"content_depth": {"score": 70.0, "raw": {"page_words": {}}}}
        recs = generate_recommendations({}, factors)
        assert not [r for r in recs if r["factor"] == "content_depth"]
        assert factors["content_depth"]["note"]

    def test_meta_without_issues_below_100_gets_a_note(self):
        factors = {
            "meta_optimization": {
                "score": 60.0,
                "raw": {
                    "completeness": "adequate",
                    "clarity": "clear",
                    "llm_optimization": "good",
                    "would_click_through": True,
                },
            }
        }
        recs = generate_recommendations({}, {}, aax_factors=factors)
        assert not [r for r in recs if r["factor"] == "meta_optimization"]
        assert factors["meta_optimization"]["note"]

    def test_factor_with_a_fix_gets_no_note(self):
        raw = {
            "coverage_pct": 40,
            "schema_types_count": 1,
            "name_consistent": True,
            "desc_consistent": True,
            "content_page_ratio": 0.5,
        }
        factors = {"topical_authority": {"score": 46.6, "raw": raw}}
        recs = generate_recommendations({}, factors)
        assert [r for r in recs if r["factor"] == "topical_authority"]
        assert "note" not in factors["topical_authority"]
