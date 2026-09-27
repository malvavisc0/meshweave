"""Tests for model-derived recommendation impact and ordering."""

from __future__ import annotations

from meshweave.scoring.composite import (
    AEO_WEIGHTS,
    GEO_WEIGHTS,
    expected_lens_delta,
    weighted_composite,
)
from meshweave.scoring.engine import compute_scores
from meshweave.scoring.recommendations import (
    generate_recommendations,
    priority_for_points,
)


def _geo_factors(**overrides: float | None) -> dict[str, dict]:
    """GEO auto factors at given scores (all auto-measured)."""
    base = {
        "topical_authority": {"score": 0.0, "raw": {}},
        "eeat": {"score": 0.0, "raw": {}},
        "crawl_access": {"score": 0.0, "raw": {}},
        "content_depth": {"score": 0.0, "raw": {}},
        "entity_consistency": {"score": 0.0, "raw": {}},
    }
    for key, score in overrides.items():
        base[key] = {"score": score, "raw": {}}
    return base


def _eeat_raw(**flags: bool) -> dict:
    return {
        "has_org_schema": flags.get("org", False),
        "has_author_info": flags.get("author", False),
    }


class TestExpectedLensDelta:
    """The counterfactual math mirrors the real composite."""

    def test_delta_matches_recomputed_composite(self):
        factors = _geo_factors(eeat=10.0, crawl_access=30.0)
        delta = expected_lens_delta("geo", factors, "eeat", 25.0)
        before = weighted_composite(factors, GEO_WEIGHTS)
        after = weighted_composite({**factors, "eeat": {"score": 25.0}}, GEO_WEIGHTS)
        assert delta == round(after - before, 1)

    def test_none_factor_introduced_at_target(self):
        """A factor currently None joins the composite at its target."""
        factors = _geo_factors()
        factors["crawl_access"] = {"score": None}
        delta = expected_lens_delta("geo", factors, "crawl_access", 23.0)
        assert delta is not None and delta > 0

    def test_unknown_lens_returns_none(self):
        assert expected_lens_delta("nope", {}, "eeat", 50.0) is None

    def test_unknown_factor_returns_none(self):
        assert expected_lens_delta("geo", _geo_factors(), "nope", 50.0) is None

    def test_no_computable_factors_returns_none(self):
        assert expected_lens_delta("geo", {}, "eeat", 50.0) is None


class TestGEORecommendationOrdering:
    """Fix order follows the model, not the generator's insertion order."""

    def _geo_zero_payload(self) -> dict:
        """A site with no schema, no sameAs, no llms.txt, no robots."""
        return {
            "audit": {
                "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
                "entity": {"same_as": [], "name_consistent": False},
            },
            "markdowns": {},
            "page": {},
            "robots": {"exists": True, "bots": {}, "sitemaps": []},
            "llms_txt": {
                "llms_txt": {"exists": False},
                "llms_full_txt": {"exists": False},
            },
        }

    def test_org_and_llms_fixes_carry_points_and_no_sameas_fix(self):
        payload = self._geo_zero_payload()
        scores = compute_scores(payload)
        geo_factors = scores["geo"]["factors"]
        geo_factors["eeat"]["raw"] = _eeat_raw()
        geo_factors["crawl_access"]["raw"] = {
            "llms_txt_exists": False,
            "robots_exists": True,
            "bot_statuses": {},
            "sitemap_count": 0,
        }

        recs = generate_recommendations({}, geo_factors, payload=payload)
        titles = [r["title"] for r in recs]

        assert "Publish an llms.txt file" in titles
        assert "Add Organization JSON-LD schema" in titles
        assert not any("sameAs" in r["title"] + r["detail"] for r in recs)
        points = [r["expected_points"] for r in recs if r["expected_points"]]
        assert points == sorted(points, reverse=True)

    def test_impact_string_renders_from_points(self):
        payload = self._geo_zero_payload()
        scores = compute_scores(payload)
        geo_factors = scores["geo"]["factors"]
        geo_factors["eeat"]["raw"] = _eeat_raw()
        geo_factors["entity_consistency"]["raw"] = {"same_as": []}
        geo_factors["crawl_access"]["raw"] = {
            "llms_txt_exists": False,
            "robots_exists": True,
            "bot_statuses": {},
            "sitemap_count": 0,
        }
        recs = generate_recommendations({}, geo_factors, payload=payload)
        llms_rec = next(r for r in recs if r["title"] == "Publish an llms.txt file")
        assert llms_rec["impact"].startswith("Reachable +")
        assert "estimated" not in llms_rec["impact"]
        assert llms_rec["expected_points"] == float(
            llms_rec["impact"].split("+")[1].split()[0]
        )

    def test_output_is_globally_sorted_by_band_then_points(self):
        """The honest prediction sorts: band first, then expected points."""
        recs = generate_recommendations(
            {}, _geo_factors(), payload=self._geo_zero_payload()
        )
        band = {"high": 0, "medium": 1, "low": 2}
        keyed = [
            (
                band.get(r["priority"], 9),
                0 if r.get("expected_points") is not None else 1,
                -(r.get("expected_points") or 0.0),
            )
            for r in recs
        ]
        assert keyed == sorted(keyed)


class TestPointlessRecommendations:
    """Recs without a counterfactual sort after point-bearing recs."""

    def test_canonical_rec_has_no_expected_points(self):
        payload = {
            "audit": {
                "meta": {"canonical_issues": ["https://a", "https://b"]},
            },
        }
        recs = generate_recommendations({}, {}, payload=payload)
        canonical = next(
            r for r in recs if r["factor"] == "schema" and "canonical" in r["title"]
        )
        assert canonical["expected_points"] is None
        assert canonical["impact"].startswith("Not scored")

    def test_unpredicted_recs_sort_after_point_bearing(self):
        payload = {
            "audit": {
                "meta": {"canonical_issues": ["https://a"]},
                "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
                "entity": {"same_as": []},
            },
            "markdowns": {},
            "page": {},
        }
        scores = compute_scores(payload)
        aeo_factors = scores["aeo"]["factors"]
        geo_factors = scores["geo"]["factors"]
        geo_factors["eeat"]["raw"] = _eeat_raw()

        recs = generate_recommendations(aeo_factors, geo_factors, payload=payload)
        with_pts = [i for i, r in enumerate(recs) if r["expected_points"] is not None]
        without_pts = [i for i, r in enumerate(recs) if r["expected_points"] is None]
        assert with_pts and without_pts
        assert max(with_pts) < min(without_pts)
        assert all(recs[i]["priority"] == "low" for i in without_pts)


class TestNonFixesDropped:
    """Praise items and 0-point predictions never reach the fix list."""

    def test_strong_schema_yields_no_praise_item(self):
        aeo_factors = {
            "schema": {
                "score": 85.0,
                "raw": {"coverage_pct": 85, "has_faq_schema": True},
            },
        }
        assert generate_recommendations(aeo_factors, {}) == []

    def test_zero_point_fix_is_dropped(self):
        aax_factors = {
            "meta_optimization": {
                "score": 100.0,
                "raw": {"improvement_suggestions": ["Shorten the title"]},
            },
            "content_delta": {"score": 100.0, "raw": {"completeness": "adequate"}},
        }
        aeo_factors = {
            "schema": {
                "score": 100.0,
                "raw": {"coverage_pct": 100, "has_faq_schema": True},
            }
        }
        recs = generate_recommendations(aeo_factors, {}, aax_factors=aax_factors)
        assert recs == []


class TestPriorityFollowsPoints:
    """Priority is derived from expected_points, never typed in."""

    def test_bands(self):
        assert priority_for_points(None) == "low"
        assert priority_for_points(0.4) == "low"
        assert priority_for_points(1.0) == "medium"
        assert priority_for_points(3.0) == "high"

    def test_every_rec_priority_matches_its_points(self):
        payload = {
            "audit": {
                "meta": {"canonical_issues": ["https://a"]},
                "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
                "entity": {},
            },
            "markdowns": {"https://a/": {"content_metrics": {"words": 100}}},
            "page": {},
        }
        scores = compute_scores(payload)
        recs = generate_recommendations(
            scores["aeo"]["factors"], scores["geo"]["factors"], payload=payload
        )
        assert recs
        for rec in recs:
            assert rec["priority"] == priority_for_points(rec["expected_points"])


class TestAEOTargets:
    """AEO rec targets track the factor's own arithmetic."""

    def test_schema_coverage_targets_80(self):
        aeo_factors = {
            "schema": {
                "score": 20.0,
                "raw": {"coverage_pct": 20, "has_faq_schema": False},
            },
        }
        recs = generate_recommendations(aeo_factors, {})
        cov = next(r for r in recs if r["title"].startswith("Add structured data"))
        delta = cov["expected_points"]
        before = weighted_composite(aeo_factors, AEO_WEIGHTS)
        after = weighted_composite(
            {**aeo_factors, "schema": {"score": 80.0}}, AEO_WEIGHTS
        )
        assert delta == round(after - before, 1)

    def test_faq_schema_bonus_targets_current_plus_10(self):
        aeo_factors = {
            "schema": {
                "score": 40.0,
                "raw": {"coverage_pct": 40, "has_faq_schema": False},
            },
        }
        recs = generate_recommendations(aeo_factors, {})
        faq = next(r for r in recs if r["title"].startswith("Add FAQPage"))
        delta = faq["expected_points"]
        before = weighted_composite(aeo_factors, AEO_WEIGHTS)
        after = weighted_composite(
            {**aeo_factors, "schema": {"score": 50.0}}, AEO_WEIGHTS
        )
        assert delta == round(after - before, 1)


class TestEndToEndPrediction:
    """A predicted delta must match the observed one through compute_scores."""

    def test_content_depth_prediction_lands(self):
        def payload(words: tuple[int, int]) -> dict:
            return {
                "audit": {},
                "markdowns": {
                    "https://a/": {"content_metrics": {"words": words[0]}},
                    "https://b/": {"content_metrics": {"words": words[1]}},
                },
                "page": {},
            }

        before = compute_scores(payload((100, 120)))
        recs = generate_recommendations({}, before["geo"]["factors"])
        depth_rec = next(r for r in recs if r["factor"] == "content_depth")
        assert "https://a/ (100 words)" in depth_rec["detail"]

        # Observed: re-score with the named pages expanded to 1,000 words;
        # the factor lands on the rec's predicted target.
        after = compute_scores(payload((1000, 1000)))
        observed = expected_lens_delta(
            "geo",
            before["geo"]["factors"],
            "content_depth",
            after["geo"]["factors"]["content_depth"]["score"],
        )
        assert depth_rec["expected_points"] == observed
