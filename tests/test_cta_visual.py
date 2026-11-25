"""Deterministic CTA visual checks: contrast, size, occlusion, competition."""

from __future__ import annotations

from dataclasses import replace

from meshweave.crawling.visual import VisualElement, VisualSnapshot
from meshweave.scoring import cta_visual
from meshweave.scoring.cta_visual import (
    AA_LARGE_TEXT,
    AA_NORMAL_TEXT,
    MAX_FINDINGS,
    assess_visual_cta,
    contrast_ratio,
    is_large_text,
    parse_color,
    text_contrast,
)


def test_parse_color_handles_rgb_rgba_and_hex() -> None:
    assert parse_color("rgb(11, 95, 255)") == cta_visual.Rgb(11, 95, 255)
    assert parse_color("rgba(10, 20, 30, 0.5)") == cta_visual.Rgb(10, 20, 30)
    assert parse_color("#0a5fff") == cta_visual.Rgb(10, 95, 255)
    assert parse_color("#abc") == cta_visual.Rgb(170, 187, 204)
    assert parse_color("transparent") is None


def test_translucent_label_colour_is_composited() -> None:
    element = _el(
        styles={"color": "rgba(255, 255, 255, 0.3)"},
        background_color="rgb(0, 0, 0)",
    )
    ratio = text_contrast(element)
    assert ratio is not None
    assert ratio < AA_NORMAL_TEXT


def test_unreadable_colour_flags_contrast_unavailable() -> None:
    element = _el(styles={"color": "device-cmyk(0 0 0 1)", "font_size": "16px"})
    codes = {finding.code for finding in assess_visual_cta(_snapshot(element)).findings}
    assert "contrast_unavailable" in codes


def test_contrast_black_on_white_is_twenty_one() -> None:
    ratio = contrast_ratio(cta_visual.Rgb(0, 0, 0), cta_visual.Rgb(255, 255, 255))
    assert ratio == 21


def test_text_contrast_uses_composited_background() -> None:
    element = _el(
        styles={"color": "rgb(200, 200, 200)"},
        background_color="rgb(255, 255, 255)",
    )
    ratio = text_contrast(element)
    assert ratio is not None
    assert ratio < AA_NORMAL_TEXT


def test_large_text_thresholds() -> None:
    assert is_large_text(_el(styles={"font_size": "24px", "font_weight": "400"}))
    assert is_large_text(_el(styles={"font_size": "19px", "font_weight": "700"}))
    assert not is_large_text(_el(styles={"font_size": "19px", "font_weight": "400"}))


def test_matches_extracted_cta_text() -> None:
    audit = _el(name="Start free audit", text="Start free audit")
    pricing = _el(name="See pricing", text="See pricing", x=400)
    result = assess_visual_cta(_snapshot(pricing, audit), cta_text="Start free audit")
    assert result.matched_by == "text"
    assert result.element is audit
    assert result.target_px == 54
    assert result.contrast_ratio is not None
    assert result.contrast_ratio >= AA_NORMAL_TEXT


def test_matching_is_word_based_not_substring() -> None:
    # "Start" is a word in "Restart now" — substring matching must not fire.
    restart = _el(name="Restart now", text="Restart now")
    result = assess_visual_cta(_snapshot(restart), cta_text="Start")
    assert result.matched_by == "prominence"

    google = _el(name="Sign in with Google", text="Sign in with Google")
    matched = assess_visual_cta(_snapshot(google), cta_text="Sign in")
    assert matched.matched_by == "text"


def test_falls_back_to_largest_button() -> None:
    big = _el(name="Book a demo", text="Book a demo", width=300, height=60)
    small = _el(name="Cookie settings", text="Cookie settings", width=120, height=30)
    result = assess_visual_cta(_snapshot(small, big))
    assert result.matched_by == "prominence"
    assert result.element is big


def test_hero_button_wins_over_larger_styled_link() -> None:
    link = _el(
        tag="a",
        href="/login",
        name="Sign in with Google",
        text="Sign in with Google",
        x=1165,
        y=15,
        width=154,
        height=40,
    )
    button = _el(name="Start analysis", text="Start analysis", x=1150, y=383)
    result = assess_visual_cta(_snapshot(link, button))
    assert result.element is button
    assert result.competing_actions == 1
    assert result.competing_names == ("Sign in with Google",)


def test_plain_nav_links_are_not_competitors() -> None:
    nav = _el(
        tag="a",
        href="/",
        name="Home",
        text="Home",
        has_own_background=False,
        x=191,
        y=10,
        width=41,
        height=49,
    )
    button = _el(name="Start analysis", text="Start analysis")
    result = assess_visual_cta(_snapshot(nav, button))
    assert result.element is button
    assert result.competing_actions == 0


def test_no_action_in_viewport() -> None:
    hidden = _el(in_viewport=False)
    result = assess_visual_cta(_snapshot(hidden))
    assert result.matched_by == "none"
    assert result.element is None
    assert result.findings[0].code == "no_action"


def test_low_contrast_small_and_occluded_are_flagged() -> None:
    element = _el(
        styles={"color": "rgb(230, 230, 230)"},
        background_color="rgb(255, 255, 255)",
        width=120,
        height=20,
        occluded=True,
    )
    result = assess_visual_cta(_snapshot(element))
    codes = {finding.code for finding in result.findings}
    assert {"text_contrast", "target_size", "occluded"} <= codes
    assert result.findings[0].severity == "high"


def test_accessible_name_must_match_visible_label() -> None:
    element = _el(name="Start analysis", text="Run Analysis")
    codes = {finding.code for finding in assess_visual_cta(_snapshot(element)).findings}
    assert "label_mismatch" in codes


def test_matching_label_is_not_flagged() -> None:
    element = _el(name="Run Analysis", text="Run Analysis")
    codes = {finding.code for finding in assess_visual_cta(_snapshot(element)).findings}
    assert "label_mismatch" not in codes


def test_below_fold_and_missing_name_are_flagged() -> None:
    element = _el(above_fold=False, has_accessible_name=False, name="", text="")
    codes = {finding.code for finding in assess_visual_cta(_snapshot(element)).findings}
    assert {"below_fold", "missing_name"} <= codes


def test_surface_contrast_against_surroundings() -> None:
    element = _el(
        background_color="rgb(250, 250, 250)",
        surrounding_color="rgb(255, 255, 255)",
    )
    codes = {finding.code for finding in assess_visual_cta(_snapshot(element)).findings}
    assert "surface_contrast" in codes


def test_competing_actions_are_counted_and_named() -> None:
    focus = _el(name="Start free audit", text="Start free audit")
    rivals = [
        _el(name="Watch demo", text="Watch demo", x=300),
        _el(name="Talk to sales", text="Talk to sales", x=600),
        _el(name="Read the docs", text="Read the docs", x=900),
    ]
    result = assess_visual_cta(_snapshot(focus, *rivals), cta_text="Start free audit")
    assert result.competing_actions == 3
    assert set(result.competing_names) == {
        "Watch demo",
        "Talk to sales",
        "Read the docs",
    }
    assert "competing_actions" in {f.code for f in result.findings}


def test_findings_are_capped_and_high_severity_first() -> None:
    element = _el(
        styles={"color": "rgb(250, 250, 250)"},
        background_color="rgb(255, 255, 255)",
        width=10,
        height=10,
        occluded=True,
        above_fold=False,
        has_accessible_name=False,
        focusable=False,
        background_estimated=True,
    )
    rivals = [_el(name=f"Action {i}", text=f"Action {i}") for i in range(5)]
    result = assess_visual_cta(_snapshot(element, *rivals), cta_text="Start free audit")
    assert result.element is element
    assert len(result.findings) <= MAX_FINDINGS
    assert result.findings[0].severity == "high"


def test_large_text_uses_relaxed_threshold() -> None:
    element = _el(styles={"font_size": "26px", "font_weight": "400"})
    result = assess_visual_cta(_snapshot(element))
    assert result.contrast_threshold == AA_LARGE_TEXT


def test_to_dict_is_json_ready() -> None:
    payload = assess_visual_cta(_snapshot(_el()), cta_text="Start free audit").to_dict()
    assert payload["element"]["name"] == "Start free audit"
    assert payload["competing_names"] == []
    assert payload["attention_share"] is None
    assert payload["attention_rank"] is None
    assert isinstance(payload["findings"], list)


def test_attention_share_and_rank_behind_competing_action() -> None:
    focus = _el(
        name="Start free audit", text="Start free audit", x=0, y=0, width=200, height=50
    )
    rival = _el(name="Watch demo", text="Watch demo", x=300, y=0)
    result = assess_visual_cta(
        _snapshot(focus, rival),
        cta_text="Start free audit",
        saliency=_FakeSaliency({(0, 0): 0.05, (300, 0): 0.12}),
    )
    assert result.attention_share == 0.05
    assert result.attention_rank == 2
    assert "attention_rank" in {finding.code for finding in result.findings}


def test_tiny_attention_is_flagged_even_when_ranked_first() -> None:
    focus = _el(name="Start free audit", x=0, y=0)
    result = assess_visual_cta(
        _snapshot(focus),
        cta_text="Start free audit",
        saliency=_FakeSaliency({(0, 0): 0.005}),
    )
    assert result.attention_rank == 1
    assert "low_attention" in {finding.code for finding in result.findings}


def test_attention_is_absent_without_a_saliency_map() -> None:
    result = assess_visual_cta(_snapshot(_el()))
    assert result.attention_share is None
    assert result.attention_rank is None


class _FakeSaliency:
    def __init__(self, shares: dict[tuple[int, int], float]) -> None:
        self._shares = shares

    def share(self, *, x: int, y: int, width: int, height: int) -> float:
        return self._shares.get((x, y), 0.0)


def _snapshot(*elements: VisualElement) -> VisualSnapshot:
    return VisualSnapshot(
        url="https://example.com",
        final_url="https://example.com/",
        viewport_width=1366,
        viewport_height=768,
        scroll_height=768,
        screenshot_png=b"",
        elements=elements,
    )


def _el(**overrides: object) -> VisualElement:
    base = VisualElement(
        tag="button",
        role="",
        name="Start free audit",
        text="Start free audit",
        href="",
        input_type="",
        x=32,
        y=296,
        width=203,
        height=54,
        above_fold=True,
        in_viewport=True,
        disabled=False,
        focusable=True,
        has_accessible_name=True,
        occluded=False,
        has_own_background=True,
        background_color="rgb(11, 95, 255)",
        background_estimated=False,
        surrounding_color="rgb(255, 255, 255)",
        styles={
            "color": "rgb(255, 255, 255)",
            "font_size": "17px",
            "font_weight": "600",
        },
    )
    return replace(base, **overrides)
