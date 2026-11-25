"""UI accessibility guards for the public site.

The visual audit flags accessible names that differ from visible labels and
primary-button text below WCAG AA contrast. These tests keep the site's own
CTA compliant.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from meshweave.scoring.cta_visual import Rgb, contrast_ratio, parse_color

ROOT = Path(__file__).resolve().parent.parent
FORM = ROOT / "webapp/templates/partials/site_crawl_form.html"
STYLE = ROOT / "webapp/static/style.css"
MIN_TEXT_CONTRAST = 4.5
MIN_SURFACE_CONTRAST = 3.0
# The dark surface the hero form and cta-banner actually sit on.
DARK_SURFACE_TOKEN = "--color-inverse-canvas"


def test_site_form_cta_accessible_name_matches_visible_label() -> None:
    soup = BeautifulSoup(FORM.read_text(), "html.parser")
    button = soup.find("button", attrs={"type": "submit"})
    assert button is not None
    label = button.get_text(strip=True)
    assert label == "Run Analysis"
    assert button.get("aria-label") in (None, label)


def _tokens() -> dict[str, str]:
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", STYLE.read_text()))


def _color(tokens: dict[str, str], name: str) -> Rgb:
    value = tokens[name].strip()
    reference = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    if reference:
        return _color(tokens, reference.group(1))
    match = re.fullmatch(r"#([0-9A-Fa-f]{6})", value)
    assert match, f"{name} is not a hex colour: {value}"
    hex_value = match.group(1)
    color = parse_color(
        f"rgb({int(hex_value[0:2], 16)}, "
        f"{int(hex_value[2:4], 16)}, {int(hex_value[4:6], 16)})"
    )
    assert color is not None
    return color


def test_primary_button_contrast_meets_aa() -> None:
    tokens = _tokens()
    foreground = _color(tokens, "--component-button-primary-text")
    surface = _color(tokens, DARK_SURFACE_TOKEN)
    for token in (
        "--component-button-primary-bg",
        "--component-button-primary-hover",
        "--component-button-primary-pressed",
    ):
        background = _color(tokens, token)
        text_ratio = contrast_ratio(foreground, background)
        surface_ratio = contrast_ratio(background, surface)
        assert text_ratio >= MIN_TEXT_CONTRAST, f"{token} text {text_ratio:.2f}:1"
        assert surface_ratio >= MIN_SURFACE_CONTRAST, (
            f"{token} surface {surface_ratio:.2f}:1"
        )
