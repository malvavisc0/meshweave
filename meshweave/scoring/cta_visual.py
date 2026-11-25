"""Deterministic visual checks for the primary action on a page.

Answers "is the CTA clear and visually accessible?" from geometry and
computed styles alone: text and surface contrast, target size, occlusion,
accessible naming, keyboard reachability, and competing above-fold actions.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from ..crawling.visual import VisualElement, VisualSnapshot
from .saliency import SaliencyMap

AA_NORMAL_TEXT = 4.5
AA_LARGE_TEXT = 3.0
MIN_TARGET_PX = 24
TOUCH_TARGET_PX = 44
COMPETITOR_MIN_AREA = 2000
MAX_COMPETITORS_LISTED = 3
MAX_FINDINGS = 6

Severity = Literal["high", "medium", "low"]
MatchMethod = Literal["text", "prominence", "none"]

_SEVERITY_ORDER: tuple[Severity, ...] = ("high", "medium", "low")
_RGB_RE = re.compile(r"rgba?\(([^)]+)\)")
_HEX_RE = re.compile(r"^#([0-9a-f]{3,8})$", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_BUTTON_TYPES = frozenset({"submit", "button"})


@dataclass(frozen=True)
class Rgb:
    """An sRGB colour with 8-bit channels."""

    r: int
    g: int
    b: int


@dataclass(frozen=True)
class VisualFinding:
    """One visual accessibility problem and its fix."""

    code: str
    severity: Severity
    detail: str
    fix: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class CtaVisualAssessment:
    """The selected action and the visual signals that decide its clarity."""

    cta_text: str | None
    matched_by: MatchMethod
    element: VisualElement | None
    contrast_ratio: float | None
    contrast_threshold: float
    surface_contrast: float | None
    target_px: int
    competing_actions: int
    competing_names: tuple[str, ...]
    attention_share: float | None
    attention_rank: int | None
    findings: tuple[VisualFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cta_text": self.cta_text,
            "matched_by": self.matched_by,
            "element": self.element.to_dict() if self.element else None,
            "contrast_ratio": self.contrast_ratio,
            "contrast_threshold": self.contrast_threshold,
            "surface_contrast": self.surface_contrast,
            "target_px": self.target_px,
            "competing_actions": self.competing_actions,
            "competing_names": list(self.competing_names),
            "attention_share": self.attention_share,
            "attention_rank": self.attention_rank,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _parse_color_alpha(value: str) -> tuple[Rgb, float] | None:
    """Parse a colour string into sRGB channels plus its alpha (0-1)."""
    text = (value or "").strip()
    match = _RGB_RE.search(text)
    if match is None:
        return _parse_hex(text)
    return _parse_rgb_functional(match.group(1))


def _parse_alpha(raw: str) -> float | None:
    """An alpha component as 0-1, percent forms allowed; None when invalid."""
    try:
        value = float(raw[:-1]) / 100 if raw.endswith("%") else float(raw)
    except ValueError:
        return None
    return value if 0.0 <= value <= 1.0 else None


def _parse_rgb_functional(body: str) -> tuple[Rgb, float] | None:
    parts = [part for part in re.split(r"[\s,/]+", body) if part]
    if len(parts) < 3:
        return None
    try:
        rgb = Rgb(*(int(float(part)) for part in parts[:3]))
    except ValueError:
        return None
    alpha = _parse_alpha(parts[3]) if len(parts) > 3 else 1.0
    if alpha is None:
        return None
    return rgb, alpha


def _parse_hex(text: str) -> tuple[Rgb, float] | None:
    match = _HEX_RE.match(text)
    if match is None:
        return None
    digits = match.group(1)
    if len(digits) in (3, 4):
        digits = "".join(char * 2 for char in digits)
    if len(digits) not in (6, 8):
        return None
    rgb = Rgb(int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
    return rgb, alpha


def parse_color(value: str) -> Rgb | None:
    """Parse a colour string — ``rgb()``, ``rgba()``, or a hex literal."""
    parsed = _parse_color_alpha(value)
    return parsed[0] if parsed is not None else None


def _channel(value: int) -> float:
    srgb = value / 255
    if srgb <= 0.04045:
        return srgb / 12.92
    return math.pow((srgb + 0.055) / 1.055, 2.4)


def relative_luminance(color: Rgb) -> float:
    """WCAG 2.x relative luminance for an sRGB colour."""
    return (
        0.2126 * _channel(color.r)
        + 0.7152 * _channel(color.g)
        + 0.0722 * _channel(color.b)
    )


def contrast_ratio(first: Rgb, second: Rgb) -> float:
    """WCAG contrast ratio between two colours, from 1.0 to 21.0."""
    high = max(relative_luminance(first), relative_luminance(second))
    low = min(relative_luminance(first), relative_luminance(second))
    return (high + 0.05) / (low + 0.05)


def _px(value: str | None) -> float:
    if not value:
        return 0.0
    match = re.match(r"\s*([\d.]+)", value)
    return float(match.group(1)) if match else 0.0


def _font_weight(value: str | None) -> int:
    if value == "bold":
        return 700
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def is_large_text(element: VisualElement) -> bool:
    """True when WCAG's relaxed 18.66px/24px large-text rule applies."""
    size = _px(element.styles.get("font_size"))
    weight = _font_weight(element.styles.get("font_weight"))
    return size >= 24 or (size >= 18.66 and weight >= 700)


def _blend(over: Rgb, base: Rgb, alpha: float) -> Rgb:
    """Composite *over* onto opaque *base* with *alpha*."""
    return Rgb(
        round(over.r * alpha + base.r * (1 - alpha)),
        round(over.g * alpha + base.g * (1 - alpha)),
        round(over.b * alpha + base.b * (1 - alpha)),
    )


def text_contrast(element: VisualElement) -> float | None:
    """Contrast between the element's text and its composited background."""
    parsed = _parse_color_alpha(element.styles.get("color", ""))
    background = parse_color(element.background_color)
    if parsed is None or background is None:
        return None
    color, alpha = parsed
    if alpha < 1.0:
        color = _blend(color, background, alpha)
    return contrast_ratio(color, background)


def surface_contrast(element: VisualElement) -> float | None:
    """Contrast between the element's own surface and what sits behind it."""
    if not element.has_own_background:
        return None
    own = parse_color(element.background_color)
    behind = parse_color(element.surrounding_color)
    if own is None or behind is None:
        return None
    return contrast_ratio(own, behind)


def _normalize(value: str) -> str:
    return _NON_WORD_RE.sub(" ", (value or "").lower()).strip()


def _token_set(value: str) -> frozenset[str]:
    return frozenset(_normalize(value).split())


def _matches(element: VisualElement, target: str) -> bool:
    """True when a label's words cover the target's words (or vice versa).

    Word-token comparison, not substring: "Start" must not match
    "Restart now", but "Sign in" matches "Sign in with Google".
    """
    wanted = _token_set(target)
    if not wanted:
        return False
    for label in (element.name, element.text):
        tokens = _token_set(label)
        if tokens and (wanted <= tokens or tokens < wanted):
            return True
    return False


def _area(element: VisualElement) -> int:
    return element.width * element.height


def _is_button(element: VisualElement) -> bool:
    if element.tag == "button" or element.role == "button":
        return True
    return element.tag == "input" and element.input_type in _BUTTON_TYPES


def _is_action(element: VisualElement) -> bool:
    """A button, or a link styled as one (own background and a label)."""
    if _is_button(element):
        return True
    return element.tag == "a" and element.has_own_background and bool(element.name)


def _focus_by_text(
    actions: list[VisualElement], cta_text: str | None
) -> VisualElement | None:
    """The best button-shaped match for the extracted label, if any."""
    target = _normalize(cta_text or "")
    if not target:
        return None
    matches = [element for element in actions if _matches(element, target)]
    if not matches:
        return None
    return min(
        matches,
        key=lambda element: (not _is_button(element), -_area(element)),
    )


def _prominent_action(actions: list[VisualElement]) -> VisualElement | None:
    """The most prominent action: a button, then a styled link, then any."""
    pool = (
        [element for element in actions if _is_button(element)]
        or [element for element in actions if _is_action(element)]
        or actions
    )
    return max(pool, key=_area) if pool else None


def _pick_focus(
    actions: list[VisualElement], cta_text: str | None
) -> tuple[VisualElement | None, MatchMethod]:
    focus = _focus_by_text(actions, cta_text)
    if focus is not None:
        return focus, "text"
    prominent = _prominent_action(actions)
    if prominent is None:
        return None, "none"
    return prominent, "prominence"


def _competitors(
    actions: list[VisualElement], focus: VisualElement | None
) -> list[VisualElement]:
    others = [
        element
        for element in actions
        if element is not focus
        and _is_action(element)
        and _area(element) >= COMPETITOR_MIN_AREA
    ]
    return sorted(others, key=_area, reverse=True)


def _finding(code: str, severity: Severity, detail: str, fix: str) -> VisualFinding:
    return VisualFinding(code=code, severity=severity, detail=detail, fix=fix)


def _contrast_findings(
    element: VisualElement, ratio: float | None, threshold: float
) -> list[VisualFinding]:
    if ratio is None:
        if (element.styles.get("color") or "").strip():
            return [
                _finding(
                    "contrast_unavailable",
                    "low",
                    "CTA contrast could not be measured: a colour is not "
                    "readable as sRGB.",
                    "Use plain sRGB colour values so contrast can be verified.",
                )
            ]
        return []
    if ratio >= threshold:
        return []
    severity: Severity = "high" if ratio < AA_LARGE_TEXT else "medium"
    return [
        _finding(
            "text_contrast",
            severity,
            f"CTA label contrast is {ratio:.1f}:1 (needs {threshold:.1f}:1).",
            "Darken the label or the background until the ratio meets the threshold.",
        )
    ]


def _surface_finding(ratio: float | None) -> list[VisualFinding]:
    if ratio is None or ratio >= AA_LARGE_TEXT:
        return []
    return [
        _finding(
            "surface_contrast",
            "medium",
            f"CTA surface blends into the page ({ratio:.1f}:1).",
            "Raise the button's contrast against the surrounding background.",
        )
    ]


def _target_finding(target_px: int) -> list[VisualFinding]:
    if target_px >= TOUCH_TARGET_PX:
        return []
    if target_px >= MIN_TARGET_PX:
        return [
            _finding(
                "touch_target",
                "low",
                f"CTA target is {target_px}px (touch guidance is {TOUCH_TARGET_PX}px).",
                f"Increase the target to at least {TOUCH_TARGET_PX}px on touch layouts.",
            )
        ]
    return [
        _finding(
            "target_size",
            "medium",
            f"CTA target is {target_px}px (minimum is {MIN_TARGET_PX}px).",
            f"Increase the target to at least {MIN_TARGET_PX}x{MIN_TARGET_PX}px.",
        )
    ]


def _element_findings(element: VisualElement) -> list[VisualFinding]:
    findings: list[VisualFinding] = []
    if element.occluded:
        findings.append(
            _finding(
                "occluded",
                "high",
                "The CTA is covered at its centre point.",
                "Move overlays, banners, or sticky elements off the CTA.",
            )
        )
    if not element.above_fold:
        findings.append(
            _finding(
                "below_fold",
                "medium",
                "The CTA is not fully visible in the first viewport.",
                "Move the action fully above the fold.",
            )
        )
    if not element.has_accessible_name:
        findings.append(
            _finding(
                "missing_name",
                "high",
                "The CTA has no visible label or accessible name.",
                "Give the action a visible label and matching accessible name.",
            )
        )
    if not element.focusable:
        findings.append(
            _finding(
                "not_focusable",
                "medium",
                "The CTA cannot receive keyboard focus.",
                "Use a native button or link, or add a focusable role.",
            )
        )
    findings.extend(_label_finding(element))
    return findings


def _label_finding(element: VisualElement) -> list[VisualFinding]:
    """Flag an accessible name that does not match the visible label."""
    if not element.name or not element.text:
        return []
    if _normalize(element.name) == _normalize(element.text):
        return []
    return [
        _finding(
            "label_mismatch",
            "medium",
            f"Accessible name {element.name!r} differs from the visible "
            f"label {element.text!r}.",
            "Make the accessible name match the visible label so agents and "
            "screen readers announce what users see.",
        )
    ]


def _competition_findings(
    competitors: list[VisualElement],
) -> list[VisualFinding]:
    if len(competitors) < 2:
        return []
    severity: Severity = "medium" if len(competitors) >= 4 else "low"
    return [
        _finding(
            "competing_actions",
            severity,
            f"{len(competitors)} other actions compete in the first viewport.",
            "Demote or remove secondary actions around the primary one.",
        )
    ]


def _estimate_finding(element: VisualElement) -> list[VisualFinding]:
    if not element.background_estimated:
        return []
    return [
        _finding(
            "contrast_estimated",
            "low",
            "Contrast is estimated because the background is an image or gradient.",
            "Check contrast against the actual rendered background.",
        )
    ]


def _attention(
    focus: VisualElement,
    competitors: list[VisualElement],
    saliency: SaliencyMap | None,
) -> tuple[float | None, int | None]:
    if saliency is None:
        return None, None
    share = saliency.share(x=focus.x, y=focus.y, width=focus.width, height=focus.height)
    ahead = sum(
        1
        for element in competitors
        if saliency.share(
            x=element.x,
            y=element.y,
            width=element.width,
            height=element.height,
        )
        > share
    )
    return round(share, 4), ahead + 1


def _attention_findings(share: float | None, rank: int | None) -> list[VisualFinding]:
    if share is None or rank is None:
        return []
    percent = share * 100
    if rank > 1:
        return [
            _finding(
                "attention_rank",
                "medium",
                f"The CTA holds {percent:.1f}% of predicted attention, "
                f"behind {rank - 1} other action(s).",
                "Reduce competing elements or strengthen the CTA's visual pull.",
            )
        ]
    if percent < 2:
        return [
            _finding(
                "low_attention",
                "low",
                f"The CTA holds only {percent:.1f}% of predicted attention.",
                "Increase the CTA's visual prominence in the first viewport.",
            )
        ]
    return []


def _rank(findings: list[VisualFinding]) -> tuple[VisualFinding, ...]:
    ordered = sorted(findings, key=lambda item: _SEVERITY_ORDER.index(item.severity))
    return tuple(ordered[:MAX_FINDINGS])


def assess_visual_cta(
    snapshot: VisualSnapshot,
    *,
    cta_text: str | None = None,
    saliency: SaliencyMap | None = None,
) -> CtaVisualAssessment:
    """Assess the primary action's visual clarity from a page snapshot.

    ``cta_text`` is the action label the content analysis extracted; when it
    matches a visible element that element is used, otherwise the most
    prominent above-fold button (then link) is chosen. ``saliency`` adds the
    CTA's predicted attention share and its rank among competing actions.
    """
    actions = [
        element
        for element in snapshot.elements
        if element.in_viewport and not element.disabled
    ]
    focus, matched_by = _pick_focus(actions, cta_text)
    if focus is None:
        return CtaVisualAssessment(
            cta_text=cta_text,
            matched_by=matched_by,
            element=None,
            contrast_ratio=None,
            contrast_threshold=AA_NORMAL_TEXT,
            surface_contrast=None,
            target_px=0,
            competing_actions=0,
            competing_names=(),
            attention_share=None,
            attention_rank=None,
            findings=(
                _finding(
                    "no_action",
                    "high",
                    "No visible action was found in the first viewport.",
                    "Place the primary action above the fold.",
                ),
            ),
        )

    threshold = AA_LARGE_TEXT if is_large_text(focus) else AA_NORMAL_TEXT
    ratio = text_contrast(focus)
    surface = surface_contrast(focus)
    target_px = min(focus.width, focus.height)
    competitors = _competitors(actions, focus)
    attention_share, attention_rank = _attention(focus, competitors, saliency)
    findings = [
        *_contrast_findings(focus, ratio, threshold),
        *_surface_finding(surface),
        *_target_finding(target_px),
        *_element_findings(focus),
        *_competition_findings(competitors),
        *_attention_findings(attention_share, attention_rank),
        *_estimate_finding(focus),
    ]
    return CtaVisualAssessment(
        cta_text=cta_text,
        matched_by=matched_by,
        element=focus,
        contrast_ratio=round(ratio, 2) if ratio is not None else None,
        contrast_threshold=threshold,
        surface_contrast=round(surface, 2) if surface is not None else None,
        target_px=target_px,
        competing_actions=len(competitors),
        competing_names=tuple(
            element.name for element in competitors[:MAX_COMPETITORS_LISTED]
        ),
        attention_share=attention_share,
        attention_rank=attention_rank,
        findings=_rank(findings),
    )
