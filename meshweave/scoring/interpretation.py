"""Result interpretation matrix for AEO/GEO/AAX score profiles.

Pure Python module — no database, no web framework dependencies.
Takes three floats (or None), returns an interpretation dict.

Profile Shape Enum Values
-------------------------

| profile_shape           | Rule | Description                   |
|-------------------------|------|-------------------------------|
| high_invisibility       | 1    | No strong lens and 2+ broken, or very low avg |
| critical_failure        | 2a   | One broken as the only sub-strong lens, avg below 65 |
| broken_in_strong_profile| 2b   | One broken as the only sub-strong lens, avg 65+ |
| material_risk           | 3    | Multiple weak/broken lenses   |
| broad_exposure          | 4    | One weak/broken + developing  |
| single_exposure         | 5    | Single weak/broken lens       |
| partial_exposure        | 6    | Two+ developing lenses        |
| developing_with_strong  | 7    | One developing, rest strong   |
| highly_readable         | 8    | All three excellent           |
| strong_profile          | 9    | All three strong or better    |
| needs_review            | 10   | Fallback                      |
| incomplete              | —    | One or more scores are None   |
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

ProfileShape = Literal[
    "high_invisibility",
    "critical_failure",
    "broken_in_strong_profile",
    "material_risk",
    "broad_exposure",
    "single_exposure",
    "partial_exposure",
    "developing_with_strong",
    "highly_readable",
    "strong_profile",
    "needs_review",
    "incomplete",
]

Tone = Literal["critical", "serious", "moderate", "limited", "positive"]

LensName = Literal["AEO", "GEO", "AAX"]

BandName = Literal["broken", "weak", "developing", "strong", "excellent"]

# ---------------------------------------------------------------------------
# Score band definitions
#
# Bands are aligned with the per-lens rating scales (ratings.py): the
# "excellent" band starts where AEO's "Fully extractable", GEO's
# "Fully connected", and AAX's "Fluent" top bands start (86+), and
# "strong" matches their 66-85 band — so a site can never be rated
# "Connected" while its band reads "developing".
# ---------------------------------------------------------------------------

_BAND_THRESHOLDS: list[tuple[float, float, BandName, str]] = [
    (0, 39, "broken", "This site's content can't be parsed by AI agents"),
    (
        40,
        59,
        "weak",
        "Missing key pieces",
    ),
    (
        60,
        69,
        "developing",
        "Got the basics, but incomplete",
    ),
    (
        70,
        85,
        "strong",
        "Good foundation in place",
    ),
    (
        86,
        100,
        "excellent",
        "Clean read, no obvious issues",
    ),
]


def _band_for(score: float) -> BandName:
    """Return the band name for a numeric score."""
    for lo, hi, band, _ in _BAND_THRESHOLDS:
        if lo <= score <= hi:
            return band
    # Fallback for out-of-range
    if score < 0:
        return "broken"
    return "excellent"


def _band_meaning(band: BandName) -> str:
    for _, _, b, meaning in _BAND_THRESHOLDS:
        if b == band:
            return meaning
    return ""


# Lens-aware meaning for the "broken" band. The generic threshold text
# ("content can't be parsed") describes an AAX failure; rendered under
# AEO or GEO it would wrongly claim unparsable content when the lens
# actually measures answer extraction or site-wide machine context.
_BROKEN_BAND_MEANINGS: dict[LensName, str] = {
    "AEO": "Agents can't extract clean, direct answers from this content",
    "GEO": "Agents can't reach or reconcile this site's content",
    "AAX": "This site's content can't be parsed by AI agents",
}


def _lens_band_meaning(lens: LensName, band: BandName) -> str:
    """Per-lens meaning for the broken band, shared meaning otherwise."""
    if band == "broken":
        return _BROKEN_BAND_MEANINGS[lens]
    return _band_meaning(band)


def _compute_bands(scores: dict[LensName, float]) -> dict[LensName, BandName]:
    """Map each lens score to its band."""
    return {lens: _band_for(score) for lens, score in scores.items()}


def _classify_profile(
    bands: dict[LensName, BandName],
    scores: dict[LensName, float],
    avg: float,
    lens_meta: dict[str, str],
) -> tuple[ProfileShape, Tone, str]:
    """Classify the profile shape via an ordered first-match rule table.

    Preserves the original 10-rule decision priority exactly.
    """
    band_counts: dict[BandName, int] = {b: 0 for b in _BAND_ORDER}
    for band in bands.values():
        band_counts[band] += 1
    broken_count = band_counts["broken"]
    weak_count = band_counts["weak"]
    developing_count = band_counts["developing"]
    strong_count = band_counts["strong"]
    excellent_count = band_counts["excellent"]
    strong_or_better_count = strong_count + excellent_count

    for rule in _PROFILE_RULES:
        shape, tone = rule.shape, rule.tone
        ctx = _RuleContext(
            broken_count=broken_count,
            weak_count=weak_count,
            developing_count=developing_count,
            strong_count=strong_count,
            excellent_count=excellent_count,
            strong_or_better_count=strong_or_better_count,
            avg=avg,
        )
        if rule.matches(ctx):
            label = _resolve_profile_label(shape, lens_meta)
            return shape, tone, label

    label = _resolve_profile_label("needs_review", lens_meta)
    return "needs_review", "moderate", label


_BAND_ORDER: tuple[BandName, ...] = (
    "broken",
    "weak",
    "developing",
    "strong",
    "excellent",
)


class _RuleContext:
    __slots__ = (
        "broken_count",
        "weak_count",
        "developing_count",
        "strong_count",
        "excellent_count",
        "strong_or_better_count",
        "avg",
    )

    def __init__(
        self,
        *,
        broken_count: int,
        weak_count: int,
        developing_count: int,
        strong_count: int,
        excellent_count: int,
        strong_or_better_count: int,
        avg: float,
    ) -> None:
        self.broken_count = broken_count
        self.weak_count = weak_count
        self.developing_count = developing_count
        self.strong_count = strong_count
        self.excellent_count = excellent_count
        self.strong_or_better_count = strong_or_better_count
        self.avg = avg


class _ProfileRule:
    __slots__ = ("shape", "tone", "matches")

    def __init__(
        self,
        shape: ProfileShape,
        tone: Tone,
        matches: Callable[[_RuleContext], bool],
    ) -> None:
        self.shape = shape
        self.tone = tone
        self.matches = matches


_PROFILE_RULES: tuple[_ProfileRule, ...] = (
    # Rule 1 — no lens is salvageable: nothing reached the strong band,
    # and either two+ lenses are broken or the average is very low with
    # at least one broken lens. A strong third lens (e.g. AEO 15, GEO 22,
    # AAX 71) must NOT be framed as "can't be parsed" — that profile
    # falls through to material_risk instead.
    _ProfileRule(
        "high_invisibility",
        "critical",
        lambda c: (
            c.strong_or_better_count == 0
            and (c.broken_count >= 2 or (c.avg < 35 and c.broken_count >= 1))
        ),
    ),
    # Rule 2a — the broken lens is the ONLY lens below strong and drags
    # the average under 65. Requires the other two lenses strong+ so the
    # "one weak spot" framing is factual; profiles with an additional
    # weak/developing lens fall through to rules 3/4.
    _ProfileRule(
        "critical_failure",
        "critical",
        lambda c: c.broken_count == 1 and c.strong_or_better_count == 2 and c.avg < 65,
    ),
    # Rule 2b — same single-broken-lens shape, but the average holds at 65+
    _ProfileRule(
        "broken_in_strong_profile",
        "serious",
        lambda c: c.broken_count == 1 and c.strong_or_better_count == 2 and c.avg >= 65,
    ),
    # Rule 3
    _ProfileRule(
        "material_risk", "serious", lambda c: c.weak_count + c.broken_count >= 2
    ),
    # Rule 4
    _ProfileRule(
        "broad_exposure",
        "serious",
        lambda c: c.weak_count + c.broken_count == 1 and c.developing_count >= 1,
    ),
    # Rule 5
    _ProfileRule(
        "single_exposure",
        "moderate",
        lambda c: c.weak_count + c.broken_count == 1,
    ),
    # Rule 6
    _ProfileRule("partial_exposure", "moderate", lambda c: c.developing_count >= 2),
    # Rule 7
    _ProfileRule(
        "developing_with_strong",
        "limited",
        lambda c: c.developing_count == 1 and c.strong_or_better_count >= 2,
    ),
    # Rule 8
    _ProfileRule("highly_readable", "positive", lambda c: c.excellent_count == 3),
    # Rule 9
    _ProfileRule("strong_profile", "positive", lambda c: c.strong_or_better_count == 3),
    # Rule 10 — fallback handled in _classify_profile
)


# ---------------------------------------------------------------------------
# Lens-aware label templates
# ---------------------------------------------------------------------------

_LENS_META: dict[LensName, dict[str, str]] = {
    "AEO": {
        "exposure": "answer structure",
        "critical_label": "no answers to find",
        "primary_exposure": "Agents fall back to generic text instead of these answers",
        "fix_priority": "Structured answers",
    },
    "GEO": {
        "exposure": "machine context",
        "critical_label": "machine context too weak",
        "primary_exposure": "Agents can't reach or reconcile this site's content",
        "fix_priority": "Crawl access + entity consistency",
    },
    "AAX": {
        "exposure": "agent experience",
        "critical_label": "Agent experience is weak",
        "primary_exposure": "AI systems may struggle to identify a credible next step",
        "fix_priority": "Offer, content, and next-step signals",
    },
}

# ---------------------------------------------------------------------------
# Headline copy (per profile shape)
# ---------------------------------------------------------------------------
_HEADLINES: dict[str, str] = {
    "high_invisibility": "AI agents can't parse the website content",
    "critical_failure": "One weak spot is pulling down the whole site",
    "broken_in_strong_profile": "Everything works except this one broken thing",
    "material_risk": "Multiple areas block a clean agent read",
    "broad_exposure": "One big gap, plus a few other issues",
    "single_exposure": "Fix this one thing and everything improves",
    "partial_exposure": "AI agents only get fragments of the website",
    "developing_with_strong": "Good shape overall — just polish {lens}",
    "highly_readable": "AI agents read this cleanly. Go check it yourself.",
    "strong_profile": "Solid foundation. Worth a quick manual check.",
    "needs_review": "Scores feel off — double-check these",
    "incomplete": "Missing scores — re-run the scan",
}

# ---------------------------------------------------------------------------
# Profile labels (per profile shape, with lens interpolation)
# ---------------------------------------------------------------------------
_PROFILE_LABELS: dict[str, str] = {
    "high_invisibility": "Content isn't usable by AI agents",
    "critical_failure": "{critical_label}",
    "broken_in_strong_profile": "{critical_label}",
    "material_risk": "Several blind spots",
    "broad_exposure": "Two areas need attention",
    "single_exposure": "{exposure} needs work",
    "partial_exposure": "Partial read across the site",
    "developing_with_strong": "{exposure} needs work",
    "highly_readable": "AI Agents read this site well",
    "strong_profile": "Solid foundation",
    "needs_review": "Unusual pattern — needs review",
    "incomplete": "Incomplete scan",
}

# ---------------------------------------------------------------------------
# Diagnosis copy — keyed by (profile_shape, weakest_lens)
#
# Single-string entries for non-lens-specific profiles.
# Dict entries for lens-specific profiles (AEO/GEO/AAX variants).
# ---------------------------------------------------------------------------
_DIAGNOSIS: dict[str, str | dict[str, str]] = {
    # Rule 1 — no lens variant
    "high_invisibility": (
        "AI agents can't work with the content. Maybe the information is there, "
        "but it's not organized for AI agents to understand and use."
    ),
    # Rule 2a — lens-specific
    "critical_failure": {
        "AEO": (
            "The content looks good, but AI agents can't pull clean answers from it. "
            "The best insights get lost in generic summaries or skipped entirely."
        ),
        "GEO": (
            "Agents can't reach or connect this site's content. Crawl access, "
            "identity consistency, and machine context are too thin to build a "
            "full picture."
        ),
        "AAX": (
            "AI systems land here but can't establish the offer or a next step. "
            "The action path is missing or unclear."
        ),
    },
    # Rule 2b — lens-specific
    "broken_in_strong_profile": {
        "AEO": (
            "The site is reachable and connected, but answers can't be "
            "extracted cleanly. The content lacks the structure agents need "
            "to pull direct answers."
        ),
        "GEO": (
            "Answers work, but the machine context is inconsistent. Agents can't "
            "reliably reach the content or reconcile the business identity "
            "across pages."
        ),
        "AAX": (
            "Answers and machine context are solid, but no clear next step is "
            "established. Agents can't find an action path."
        ),
    },
    # Rule 3 — no lens variant
    "material_risk": (
        "Agents get fragments, not the whole picture. Answers, machine context, "
        "or action paths are missing in multiple places."
    ),
    # Rule 4 — lens-specific
    "broad_exposure": {
        "AEO": (
            "Answers can't be extracted, plus another area needs attention. "
            "Agents don't have enough to work with in two places."
        ),
        "GEO": (
            "Machine context is inconsistent and another area is still developing. "
            "Agents can't reliably reach and connect the content."
        ),
        "AAX": (
            "The action path is unclear here and another area isn't ready. "
            "Agents can't establish a next step."
        ),
    },
    # Rule 5 — lens-specific
    "single_exposure": {
        "AEO": (
            "The rest of the profile holds up. Fix answer structure and "
            "everything improves fast."
        ),
        "GEO": (
            "Answers work, but the machine context is inconsistent. Identity, "
            "authorship, and policy consistency need work."
        ),
        "AAX": (
            "Answers and machine context work, but the offer and next step are "
            "unclear. Agents can't find a clear action path."
        ),
    },
    # Rule 6 — no lens variant
    "partial_exposure": (
        "Agents read parts of the website, but the full picture's missing. "
        "Multiple areas are 'almost there' — not enough for a clean, "
        "complete read."
    ),
    # Rule 7 — lens-specific
    "developing_with_strong": {
        "AEO": (
            "Reachability and action paths are solid. Answer structure is the "
            "only area holding this site back."
        ),
        "GEO": (
            "Answers and action paths are solid. Machine context needs "
            "strengthening — consistency and crawl access."
        ),
        "AAX": "Answers and machine context are solid. The offer and next step need clarification.",
    },
    # Rule 8 — no lens variant
    "highly_readable": (
        "Agents read this site cleanly — content, context, and next steps "
        "all hold up. Run self-verification to be sure."
    ),
    # Rule 9 — no lens variant
    "strong_profile": (
        "Strong scores across the board. Manual verification still matters — "
        "check the details yourself before acting."
    ),
    # Rule 10 — no lens variant
    "needs_review": (
        "These scores don't match typical patterns. Check individual factors before deciding next steps."
    ),
    # Incomplete — no lens variant
    "incomplete": (
        "Need all three scores for a full picture. "
        "Re-run the crawl or check for scoring errors."
    ),
}

# ---------------------------------------------------------------------------
# Next step recommendation (always present)
# ---------------------------------------------------------------------------

_NEXT_STEP: str = "A few things only a human can verify — run through those before treating this as final."


def _resolve_diagnosis(
    profile_shape: str,
    weakest_lens: LensName | None,
) -> str:
    """Return the diagnosis string for a profile shape."""
    entry = _DIAGNOSIS.get(profile_shape, "")
    if isinstance(entry, dict) and weakest_lens:
        text = entry.get(weakest_lens, "")
    elif isinstance(entry, str):
        text = entry
    else:
        text = ""

    # Interpolate {lens} placeholder in diagnosis text
    if "{lens}" in text and weakest_lens:
        lens_meta = _LENS_META.get(weakest_lens, {})
        text = text.replace("{lens}", lens_meta.get("exposure", weakest_lens))

    return text


def _resolve_profile_label(
    profile_shape: str,
    lens_meta: dict[str, str],
) -> str:
    """Return the profile label with substitutions."""
    default = "Score pattern needs review"
    template = _PROFILE_LABELS.get(profile_shape, default)
    if "{critical_label}" in template:
        template = template.replace(
            "{critical_label}", lens_meta.get("critical_label", "")
        )
    if "{exposure}" in template:
        template = template.replace("{exposure}", lens_meta.get("exposure", ""))
    # Ensure the resolved label starts with an uppercase letter
    if template:
        return template[0].upper() + template[1:]
    return template


def _resolve_headline(
    profile_shape: str,
    lens_meta: dict[str, str],
) -> str:
    """Return the headline with lens-specific substitutions."""
    template = _HEADLINES.get(profile_shape, "")
    if "{lens}" in template:
        lens_name = lens_meta.get("exposure", "visibility")
        template = template.replace("{lens}", lens_name)
    return template


def interpret_profile(
    aeo_score: float | None,
    geo_score: float | None,
    aax_score: float | None,
) -> dict:
    """Classify the shape of an AEO/GEO/AAX score profile.

    Args:
        aeo_score: AEO composite score (0-100) or None.
        geo_score: GEO composite score (0-100) or None.
        aax_score: AAX composite score (0-100) or None.

    Returns:
        Interpretation dict with profile_label, tone, headline, diagnosis,
        weakest_lens, strongest_lens, primary_exposure, fix_priority, bands,
        profile_shape, lens_details, next_step.
    """
    # ------------------------------------------------------------------
    # None handling — return minimal fallback
    # ------------------------------------------------------------------
    all_scores = [aeo_score, geo_score, aax_score]
    if any(s is None for s in all_scores):
        return {
            "profile_label": "Incomplete data",
            "tone": "moderate",
            "headline": "One or more scores couldn't be calculated.",
            "diagnosis": (
                "We need all three scores for a full picture. "
                "Re-run the crawl or check for scoring errors."
            ),
            "weakest_lens": None,
            "strongest_lens": None,
            "primary_exposure": None,
            "fix_priority": None,
            "bands": {},
            "profile_shape": "incomplete",
            "lens_details": {},
            "next_step": _NEXT_STEP,
        }

    # ------------------------------------------------------------------
    # Score bands (per lens)
    # ------------------------------------------------------------------
    assert aeo_score is not None
    assert geo_score is not None
    assert aax_score is not None
    scores: dict[LensName, float] = {
        "AEO": aeo_score,
        "GEO": geo_score,
        "AAX": aax_score,
    }
    bands: dict[LensName, BandName] = _compute_bands(scores)

    # Weakest and strongest lenses
    weakest_lens: LensName = min(scores, key=lambda k: scores[k])
    strongest_lens: LensName = max(scores, key=lambda k: scores[k])

    # Lens metadata
    lens_meta = _LENS_META[weakest_lens]

    avg = sum(scores.values()) / 3.0

    # ------------------------------------------------------------------
    # Profile shape decision table (first match wins)
    # ------------------------------------------------------------------
    profile_shape, tone, profile_label = _classify_profile(
        bands, scores, avg, lens_meta
    )

    # ------------------------------------------------------------------
    # Headline
    # ------------------------------------------------------------------
    headline = _resolve_headline(profile_shape, lens_meta)

    # ------------------------------------------------------------------
    # Diagnosis
    # ------------------------------------------------------------------
    diagnosis = _resolve_diagnosis(profile_shape, weakest_lens)

    # ------------------------------------------------------------------
    # Lens details
    # ------------------------------------------------------------------
    lens_details: dict[str, dict] = {}
    for lens in ("AEO", "GEO", "AAX"):
        band: BandName = bands[lens]
        label = band.capitalize()
        lens_details[lens] = {
            "band_label": label,
            "meaning": _lens_band_meaning(lens, band),
        }

    # ------------------------------------------------------------------
    # Build return dict
    # ------------------------------------------------------------------
    return {
        "profile_label": profile_label,
        "tone": tone,
        "headline": headline,
        "diagnosis": diagnosis,
        "weakest_lens": weakest_lens,
        "strongest_lens": strongest_lens,
        "primary_exposure": lens_meta.get("primary_exposure"),
        "fix_priority": lens_meta.get("fix_priority"),
        "bands": {
            lens: {"score": scores[lens], "band": band_value}
            for lens, band_value in bands.items()
        },
        "profile_shape": profile_shape,
        "lens_details": lens_details,
        "next_step": _NEXT_STEP,
    }
