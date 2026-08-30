"""Tests for the interpretation matrix module."""

import pytest

from meshweave.scoring.interpretation import _band_for, _band_meaning, interpret_profile


class TestBandFor:
    """Test score band classification."""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0, "broken"),
            (25, "broken"),
            (39, "broken"),
            (40, "weak"),
            (50, "weak"),
            (59, "weak"),
            (60, "developing"),
            (62, "developing"),
            (69, "developing"),
            (70, "strong"),
            (78, "strong"),
            (85, "strong"),
            (86, "excellent"),
            (92, "excellent"),
            (100, "excellent"),
        ],
    )
    def test_band_classification(self, score, expected):
        assert _band_for(score) == expected

    def test_negative_score(self):
        assert _band_for(-5) == "broken"

    def test_over_100(self):
        assert _band_for(105) == "excellent"


class TestBandMeaning:
    """Test band meaning lookup."""

    def test_broken_meaning(self):
        assert "can't" in _band_meaning("broken").lower()

    def test_weak_meaning(self):
        assert "missing" in _band_meaning("weak").lower()

    def test_developing_meaning(self):
        assert "basics" in _band_meaning("developing").lower()

    def test_strong_meaning(self):
        assert "foundation" in _band_meaning("strong").lower()

    def test_excellent_meaning(self):
        assert "clean" in _band_meaning("excellent").lower()


class TestInterpretProfile:
    """Test the main interpret_profile function."""

    def test_none_handling(self):
        result = interpret_profile(80.0, None, 75.0)
        assert result["profile_shape"] == "incomplete"
        assert result["tone"] == "moderate"
        assert result["profile_label"] == "Incomplete data"

    def test_all_none(self):
        result = interpret_profile(None, None, None)
        assert result["profile_shape"] == "incomplete"
        assert result["limitations"]  # auto-only has limitations

    def test_score_basis_full(self):
        result = interpret_profile(80.0, 70.0, 75.0, score_basis="full")
        assert result["score_basis"] == "full"
        assert result["limitations"] == []

    def test_score_basis_auto(self):
        result = interpret_profile(80.0, 70.0, 75.0, score_basis="auto")
        assert result["score_basis"] == "auto"
        assert len(result["limitations"]) > 0

    def test_next_step_always_present(self):
        result = interpret_profile(80.0, 70.0, 75.0)
        assert "next_step" in result
        assert len(result["next_step"]) > 0


class TestProfileShapes:
    """Test each profile shape rule."""

    # Rule 1: High invisibility
    def test_rule1_two_broken(self):
        result = interpret_profile(20.0, 25.0, 60.0)
        assert result["profile_shape"] == "high_invisibility"
        assert result["tone"] == "critical"

    def test_rule1_low_avg(self):
        # avg 35 with one broken lens → high invisibility
        result = interpret_profile(30.0, 35.0, 40.0)
        assert result["profile_shape"] == "high_invisibility"
        assert result["tone"] == "critical"

    # Rule 2a: Critical failure, avg < 65
    def test_rule2a_broken_low_avg(self):
        result = interpret_profile(30.0, 72.0, 80.0)
        avg = (30.0 + 72.0 + 80.0) / 3.0
        assert avg < 65
        assert result["profile_shape"] == "critical_failure"
        assert result["tone"] == "critical"

    # Rule 2b: Broken in strong profile, avg >= 65
    def test_rule2b_broken_high_avg(self):
        result = interpret_profile(38.0, 80.0, 85.0)
        avg = (38.0 + 80.0 + 85.0) / 3.0
        assert avg >= 65
        assert result["profile_shape"] == "broken_in_strong_profile"
        assert result["tone"] == "serious"

    # Rule 3: Material risk
    def test_rule3_two_weak(self):
        result = interpret_profile(45.0, 50.0, 75.0)
        assert result["profile_shape"] == "material_risk"
        assert result["tone"] == "serious"

    # Rule 4: Broad exposure
    def test_rule4_weak_and_developing(self):
        result = interpret_profile(48.0, 60.0, 78.0)
        assert result["profile_shape"] == "broad_exposure"
        assert result["tone"] == "serious"

    # Rule 5: Single exposure
    def test_rule5_single_weak(self):
        result = interpret_profile(48.0, 75.0, 80.0)
        assert result["profile_shape"] == "single_exposure"
        assert result["tone"] == "moderate"

    # Rule 6: Partial exposure
    def test_rule6_two_developing(self):
        # 60-69 band is "developing" since the band alignment; 58 is weak.
        result = interpret_profile(62.0, 65.0, 75.0)
        assert result["profile_shape"] == "partial_exposure"
        assert result["tone"] == "moderate"

    # Rule 7: Developing with strong
    def test_rule7_one_developing_two_strong(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        assert result["profile_shape"] == "developing_with_strong"
        assert result["tone"] == "limited"

    # Rule 8: Highly readable
    def test_rule8_all_excellent(self):
        result = interpret_profile(88.0, 91.0, 86.0)
        assert result["profile_shape"] == "highly_readable"
        assert result["tone"] == "positive"

    # Rule 9: Strong profile
    def test_rule9_all_strong(self):
        result = interpret_profile(72.0, 75.0, 78.0)
        assert result["profile_shape"] == "strong_profile"
        assert result["tone"] == "positive"

    # Rule 10: Fallback
    # Note: With 3 lenses, Rule 10 is theoretically unreachable
    # because all score combinations match rules 1-9.
    # The fallback exists as a safety net for edge cases.
    def test_rule10_unreachable(self):
        # Verify Rule 6 catches what was previously tested as Rule 10.
        # Bands changed (weak now extends to 59), so use developing-only
        # scores: 62 + 65 + 68 — no weak/broken lens.
        result = interpret_profile(62.0, 65.0, 68.0)
        assert result["profile_shape"] == "partial_exposure"
        assert result["tone"] == "moderate"


class TestStrongLensNotCatastrophic:
    """A strong lens must not be framed as 'can't be parsed'.

    Regression: candlebar.app scored AEO 15, GEO 22, AAX 71.2 and got
    the high_invisibility headline "AI agents can't parse the website
    content" — directly contradicting the strong AAX lens and its
    agent-readout summary on the same page.
    """

    def test_two_broken_one_strong_is_material_risk(self):
        result = interpret_profile(15.0, 22.0, 71.2)
        assert result["profile_shape"] == "material_risk"
        assert result["tone"] == "serious"
        assert "can't parse" not in result["headline"].lower()

    def test_low_avg_with_strong_lens_not_high_invisibility(self):
        # avg < 35, but the strong AAX lens keeps the profile out of
        # the catastrophic "no lens is salvageable" framing.
        result = interpret_profile(10.0, 15.0, 70.0)
        assert result["profile_shape"] == "material_risk"

    def test_all_broken_still_high_invisibility(self):
        result = interpret_profile(15.0, 22.0, 25.0)
        assert result["profile_shape"] == "high_invisibility"
        assert result["tone"] == "critical"

    def test_developing_third_lens_keeps_high_invisibility(self):
        # Nothing strong-or-better, two broken → still catastrophic.
        result = interpret_profile(20.0, 25.0, 60.0)
        assert result["profile_shape"] == "high_invisibility"


class TestSingleProblemLensIsolation:
    """'One weak spot' copy must only fire when there is exactly one."""

    def test_broken_plus_weak_is_material_risk(self):
        # Regression: cloakbrowser.dev scored AEO 46.1, GEO 36.3,
        # AAX 78.5 and was framed as "One weak spot is affecting the
        # visibility of the whole site" despite two lenses below par.
        result = interpret_profile(46.1, 36.3, 78.5)
        assert result["profile_shape"] == "material_risk"
        assert result["tone"] == "serious"

    def test_broken_plus_developing_is_broad_exposure(self):
        result = interpret_profile(30.0, 62.0, 80.0)
        assert result["profile_shape"] == "broad_exposure"

    def test_lone_broken_between_two_strong_still_2a(self):
        result = interpret_profile(30.0, 72.0, 80.0)
        assert result["profile_shape"] == "critical_failure"


class TestLensSpecificLabels:
    """Test lens-specific label substitution."""

    def test_aeo_weakest_label(self):
        # Use scores that trigger Rule 2a (critical_failure)
        # where the label is lens-specific
        result = interpret_profile(30.0, 72.0, 80.0)
        assert result["weakest_lens"] == "AEO"
        label = result["profile_label"].lower()
        assert "answer" in label or "citation" in label

    def test_geo_weakest_label(self):
        result = interpret_profile(72.0, 30.0, 80.0)
        assert result["weakest_lens"] == "GEO"
        label = result["profile_label"].lower()
        assert "trust" in label or "recommend" in label

    def test_aax_weakest_label(self):
        result = interpret_profile(72.0, 80.0, 30.0)
        assert result["weakest_lens"] == "AAX"
        label = result["profile_label"].lower()
        assert "agent" in label or "usability" in label


class TestReturnStructure:
    """Test that all required fields are present."""

    def test_all_fields_present(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        required_fields = [
            "profile_label",
            "tone",
            "headline",
            "diagnosis",
            "weakest_lens",
            "strongest_lens",
            "primary_exposure",
            "fix_priority",
            "bands",
            "profile_shape",
            "lens_details",
            "score_basis",
            "limitations",
            "next_step",
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"

    def test_bands_structure(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        for lens in ("AEO", "GEO", "AAX"):
            assert lens in result["bands"]
            assert "score" in result["bands"][lens]
            assert "band" in result["bands"][lens]

    def test_lens_details_structure(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        for lens in ("AEO", "GEO", "AAX"):
            assert lens in result["lens_details"]
            assert "band_label" in result["lens_details"][lens]
            assert "meaning" in result["lens_details"][lens]

    def test_incomplete_bands_empty(self):
        result = interpret_profile(None, 62.0, 77.0)
        assert result["bands"] == {}
        assert result["lens_details"] == {}


class TestCapitalization:
    """Test that profile labels always start with an uppercase letter."""

    def test_developing_with_strong_geo_capitalized(self):
        """The bug: GEO exposure 'recommendation signals' was lowercase."""
        result = interpret_profile(82.3, 62.0, 77.1)
        assert result["profile_label"][0].isupper()
        assert result["profile_label"] == "Trust signals needs work"

    def test_developing_with_strong_aeo_capitalized(self):
        result = interpret_profile(62.0, 82.0, 77.0)
        assert result["profile_label"][0].isupper()

    def test_developing_with_strong_aax_capitalized(self):
        result = interpret_profile(82.0, 77.0, 62.0)
        assert result["profile_label"][0].isupper()

    def test_single_exposure_geo_capitalized(self):
        result = interpret_profile(75.0, 48.0, 80.0)
        assert result["profile_label"][0].isupper()

    def test_critical_failure_capitalized(self):
        result = interpret_profile(72.0, 30.0, 80.0)
        assert result["profile_label"][0].isupper()


class TestBoundaryScores:
    """Test boundary conditions in the decision table."""

    def test_avg_exactly_45_not_rule1(self):
        """avg == 45 should NOT trigger Rule 1 (which requires avg < 45)."""
        # 30 + 45 + 60 = 135 / 3 = 45.0, but 30 is broken so check shape
        # We need: no two broken, avg == 45 exactly
        # 40 + 45 + 50 = 135 / 3 = 45.0 — all weak, no broken
        result = interpret_profile(40.0, 45.0, 50.0)
        assert result["profile_shape"] != "high_invisibility"

    def test_avg_just_below_45_without_broken_is_not_rule1(self):
        """Three uniform sub-45 scores with no broken lens are weak, not
        catastrophic — Rule 1's average clause requires a broken lens."""
        # 40 + 44 + 50 = 134 / 3 = 44.67 — all weak, no broken
        result = interpret_profile(40.0, 44.0, 50.0)
        assert result["profile_shape"] != "high_invisibility"

    def test_avg_below_35_with_broken_is_rule1(self):
        # 30 + 34 + 40 = 104 / 3 = 34.67, one broken
        result = interpret_profile(30.0, 34.0, 40.0)
        assert result["profile_shape"] == "high_invisibility"

    def test_avg_exactly_65_is_rule2b(self):
        """avg == 65 should trigger Rule 2b (avg >= 65), not 2a."""
        # Need 1 broken, avg = 65: e.g. 39 + 78 + 78 = 195 / 3 = 65
        result = interpret_profile(39.0, 78.0, 78.0)
        assert result["profile_shape"] == "broken_in_strong_profile"

    def test_avg_just_below_65_is_rule2a(self):
        # 39 + 77 + 77 = 193 / 3 = 64.33
        result = interpret_profile(39.0, 77.0, 77.0)
        assert result["profile_shape"] == "critical_failure"

    def test_score_at_band_boundary_39_40(self):
        assert _band_for(39) == "broken"
        assert _band_for(40) == "weak"

    def test_score_at_band_boundary_59_60(self):
        assert _band_for(59) == "weak"
        assert _band_for(60) == "developing"

    def test_score_at_band_boundary_69_70(self):
        assert _band_for(69) == "developing"
        assert _band_for(70) == "strong"

    def test_score_at_band_boundary_85_86(self):
        assert _band_for(85) == "strong"
        assert _band_for(86) == "excellent"


class TestTiedScores:
    """Test behavior when lenses have identical scores."""

    def test_all_three_tied(self):
        result = interpret_profile(75.0, 75.0, 75.0)
        assert result["weakest_lens"] is not None
        assert result["strongest_lens"] is not None
        assert result["profile_shape"] == "strong_profile"

    def test_two_tied_weakest(self):
        result = interpret_profile(60.0, 60.0, 80.0)
        # Should still produce a valid result; weakest is AEO (first in dict)
        assert result["weakest_lens"] in ("AEO", "GEO")
        assert result["profile_shape"] == "partial_exposure"

    def test_two_tied_strongest(self):
        result = interpret_profile(48.0, 80.0, 80.0)
        assert result["strongest_lens"] in ("GEO", "AAX")


class TestDiagnosisContent:
    """Test that diagnosis text is non-empty and lens-appropriate."""

    def test_diagnosis_non_empty_for_all_shapes(self):
        test_cases = [
            (20.0, 25.0, 60.0, "high_invisibility"),
            (30.0, 72.0, 80.0, "critical_failure"),
            (38.0, 80.0, 85.0, "broken_in_strong_profile"),
            (45.0, 50.0, 75.0, "material_risk"),
            (48.0, 60.0, 78.0, "broad_exposure"),
            (48.0, 75.0, 80.0, "single_exposure"),
            (62.0, 65.0, 75.0, "partial_exposure"),
            (82.0, 62.0, 77.0, "developing_with_strong"),
            (88.0, 91.0, 86.0, "highly_readable"),
            (72.0, 75.0, 78.0, "strong_profile"),
        ]
        for aeo, geo, aax, expected_shape in test_cases:
            result = interpret_profile(aeo, geo, aax)
            assert result["profile_shape"] == expected_shape, (
                f"Expected {expected_shape} for ({aeo}, {geo}, {aax})"
            )
            assert len(result["diagnosis"]) > 0, f"Empty diagnosis for {expected_shape}"

    def test_geo_diagnosis_mentions_recommendation(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        assert result["weakest_lens"] == "GEO"
        assert "trust" in result["diagnosis"].lower()

    def test_aeo_diagnosis_mentions_answer(self):
        result = interpret_profile(30.0, 72.0, 80.0)
        assert result["weakest_lens"] == "AEO"
        assert "answer" in result["diagnosis"].lower()


class TestHeadlineInterpolation:
    """Test headline {lens} placeholder substitution."""

    def test_rule7_headline_contains_lens_name(self):
        """Rule 7 headline has {lens} — should be replaced."""
        result = interpret_profile(82.0, 62.0, 77.0)
        assert result["profile_shape"] == "developing_with_strong"
        assert "{lens}" not in result["headline"]
        assert "trust signals" in result["headline"]

    def test_rule7_aeo_headline(self):
        result = interpret_profile(62.0, 82.0, 77.0)
        assert "{lens}" not in result["headline"]
        assert "answer structure" in result["headline"]

    def test_rule7_aax_headline(self):
        result = interpret_profile(82.0, 77.0, 62.0)
        assert "{lens}" not in result["headline"]
        assert "agent experience" in result["headline"]


class TestLensDetailsLabels:
    """Test that lens_details band_label is properly capitalized."""

    def test_band_labels_capitalized(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        for lens in ("AEO", "GEO", "AAX"):
            label = result["lens_details"][lens]["band_label"]
            assert label[0].isupper(), f"{lens} band_label not capitalized"

    def test_band_labels_match_bands(self):
        result = interpret_profile(82.0, 62.0, 77.0)
        for lens in ("AEO", "GEO", "AAX"):
            band = result["bands"][lens]["band"]
            label = result["lens_details"][lens]["band_label"]
            assert label.lower() == band

    def test_geo_broken_meaning_is_lens_specific(self):
        # GEO broken is a trust failure, not an unparsable-content claim.
        result = interpret_profile(15.0, 22.0, 71.2)
        meaning = result["lens_details"]["GEO"]["meaning"].lower()
        assert "parse" not in meaning
        assert "recommend" in meaning

    def test_aax_broken_meaning_keeps_parsing_claim(self):
        result = interpret_profile(15.0, 22.0, 25.0)
        meaning = result["lens_details"]["AAX"]["meaning"].lower()
        assert "parse" in meaning


class TestIncompleteVariants:
    """Additional incomplete/None edge cases."""

    def test_incomplete_full_basis_no_limitations(self):
        result = interpret_profile(None, 62.0, 77.0, score_basis="full")
        assert result["limitations"] == []

    def test_single_none_first(self):
        result = interpret_profile(None, 80.0, 80.0)
        assert result["profile_shape"] == "incomplete"

    def test_single_none_last(self):
        result = interpret_profile(80.0, 80.0, None)
        assert result["profile_shape"] == "incomplete"
