"""Result interpretation matrix for AEO/GEO/AAX score profiles.

Pure Python module — no database, no web framework dependencies.
Takes three floats (or None), returns an interpretation dict.

Profile Shape Enum Values
-------------------------

| profile_shape           | Rule | Description                   |
|-------------------------|------|-------------------------------|
| high_invisibility       | 1    | Two+ broken or very low avg   |
| critical_failure        | 2a   | One broken, avg below 65      |
| broken_in_strong_profile| 2b   | One broken in strong profile   |
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
# "excellent" band starts where AEO/GEO's "Excellent"/"Dominant" and AAX's
# "Fluent" top bands start (86+), and "strong" matches their 70-85 band —
# so a site can never be rated "Authoritative" while its band reads
# "developing".
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
    # Rule 1 — two+ broken lenses, or an average so low that no lens is
    # salvageable. The average clause requires < 35 *and* at least one
    # broken lens, so a site with three uniform 44s (no broken lens) is
    # not framed as catastrophic "can't be parsed" — it falls through to
    # the material_risk/needs_review rules instead.
    _ProfileRule(
        "high_invisibility",
        "critical",
        lambda c: c.broken_count >= 2 or (c.avg < 35 and c.broken_count >= 1),
    ),
    # Rule 2a — broken lens, avg < 65
    _ProfileRule(
        "critical_failure", "critical", lambda c: c.broken_count == 1 and c.avg < 65
    ),
    # Rule 2b — broken lens, avg >= 65
    _ProfileRule(
        "broken_in_strong_profile",
        "serious",
        lambda c: c.broken_count == 1 and c.avg >= 65,
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
        "gap": "answers aren't citeable",
        "exposure": "answer structure",
        "failure": "answers fall apart",
        "critical_label": "no answers to find",
        "primary_exposure": "Lower likelihood of being quoted",
        "fix_priority": "Structured answers",
    },
    "GEO": {
        "gap": "no one recommends this brand",
        "exposure": "trust signals",
        "failure": "invisible to recommenders",
        "critical_label": "trust factor too low",
        "primary_exposure": "Lower likelihood of appearing in AI results",
        "fix_priority": "Entity + trust signals",
    },
    "AAX": {
        "gap": "Next-step signals need work",
        "exposure": "agent experience",
        "failure": "Next-step signals are weak",
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
    "critical_failure": "One weak spot is affecting the visibility of the whole site",
    "broken_in_strong_profile": "Everything works except this one broken thing",
    "material_risk": "AI agents see the site but can't fully trust it",
    "broad_exposure": "One big gap, plus a few other issues",
    "single_exposure": "Fix this one thing and everything improves",
    "partial_exposure": "AI agents only get fragments of the website",
    "developing_with_strong": "Good shape overall — {lens} just needs polish",
    "highly_readable": "AI agents read this cleanly. Go check it yourself.",
    "strong_profile": "Solid foundation. Quick check recommended.",
    "needs_review": "Scores feel off — double-check these",
    "incomplete": "Missing scores — re-run the scan",
}

# ---------------------------------------------------------------------------
# Profile labels (per profile shape, with lens interpolation)
# ---------------------------------------------------------------------------
_PROFILE_LABELS: dict[str, str] = {
    "high_invisibility": "Can't be found by AI",
    "critical_failure": "{critical_label}",
    "broken_in_strong_profile": "{critical_label}",
    "material_risk": "Several blind spots",
    "broad_exposure": "Two areas need attention",
    "single_exposure": "{exposure} needs work",
    "partial_exposure": "Partially visible",
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
            "When people ask AI agents for recommendations, "
            "this brand is unlikely to surface. Missing entity data, authority signals, and social proof."
        ),
        "AAX": (
            "AI systems land here but struggle with the offer and next steps. "
            "Lower likelihood of a credible next step."
        ),
    },
    # Rule 2b — lens-specific
    "broken_in_strong_profile": {
        "AEO": (
            "Trust signals are decent, but answer structure falls apart. "
            "The content gets ignored in AI responses."
        ),
        "GEO": (
            "Answers work, but AI agents don't see enough trust or "
            "authority signals to recommend the brand. Competitors with clearer "
            "entity data will get picked instead."
        ),
        "AAX": (
            "Answer and trust signals are solid, but recommendation signals need attention. "
            "Lower likelihood of a credible next step."
        ),
    },
    # Rule 3 — no lens variant
    "material_risk": (
        "AI grabs fragments but can't see the whole story. "
        "Key content, trust signals, or next-step signals are missing in multiple places."
    ),
    # Rule 4 — lens-specific
    "broad_exposure": {
        "AEO": (
            "Answers aren't working, plus another area needs attention. "
            "AI doesn't have enough to go on in two places."
        ),
        "GEO": (
            "Trust signals are weak and another area is still developing. "
            "AI doesn't have enough reason to recommend this brand over alternatives."
        ),
        "AAX": (
            "Friction appears here and another area isn't ready. "
            "Lower likelihood of a credible next step."
        ),
    },
    # Rule 5 — lens-specific
    "single_exposure": {
        "AEO": (
            "Trust signals are strong. But fix answers and everything improves fast."
        ),
        "GEO": (
            "Answers work, but AI systems don't see enough trust signals to "
            "recommend the brand. Entity data, author info, and social proof need work."
        ),
        "AAX": (
            "Answer and trust signals work, but the offer and next-step signals are "
            "unclear. AI systems may struggle to identify a credible next step."
        ),
    },
    # Rule 6 — no lens variant
    "partial_exposure": (
        "AI reads parts of the website, but the full picture's missing. "
        "Multiple areas are 'almost there'—not enough for confident AI action."
    ),
    # Rule 7 — lens-specific
    "developing_with_strong": {
        "AEO": (
            "Trust signals are strong. Answer structure is the only area holding this site back."
        ),
        "GEO": (
            "Answers work. Trust signals need strengthening for AI recommendations."
        ),
        "AAX": "Answers and trust are solid. Offer and next-step signals need clarification.",
    },
    # Rule 8 — no lens variant
    "highly_readable": (
        "AI reads the content well. Citations, recommendations, and signals look strong. "
        "Run self-verification to be sure."
    ),
    # Rule 9 — no lens variant
    "strong_profile": (
        "Strong scores across the board. Manual verification needed for citation accuracy, competitive positioning, or capture rate."
    ),
    # Rule 10 — no lens variant
    "needs_review": (
        "These scores don't match typical patterns. Check individual factors before deciding next steps."
    ),
    # Incomplete — no lens variant
    "incomplete": (
        "Need all three scores (AEO, GEO, AAX) for a full picture. "
        "Re-run the crawl or check for scoring errors."
    ),
}

# ---------------------------------------------------------------------------
# Limitations for auto-only scans (what the free scan doesn't show)
# ---------------------------------------------------------------------------

_AUTO_ONLY_LIMITATIONS: list[str] = [
    "Citation frequency — requires checking manually",
    "Query-match relevance — requires query-match analysis",
    "Competitor comparison — requires competitive analysis",
    "Voice assistant presence — requires testing through voice",
    "Capture rate accuracy — requires Search Console data",
]

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
    if "{failure}" in template:
        template = template.replace("{failure}", lens_meta.get("failure", ""))
    if "{critical_label}" in template:
        template = template.replace(
            "{critical_label}", lens_meta.get("critical_label", "")
        )
    if "{exposure}" in template:
        template = template.replace("{exposure}", lens_meta.get("exposure", ""))
    if "{gap_label}" in template:
        template = template.replace("{gap_label}", lens_meta.get("gap", ""))
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
    *,
    score_basis: str = "auto",
) -> dict:
    """Classify the shape of an AEO/GEO/AAX score profile.

    Args:
        aeo_score: AEO composite score (0-100) or None.
        geo_score: GEO composite score (0-100) or None.
        aax_score: AAX composite score (0-100) or None.
        score_basis: "auto" for free scans (auto-only composites),
                     "full" for paid audits (complete composites).

    Returns:
        Interpretation dict with profile_label, tone, headline, diagnosis,
        weakest_lens, strongest_lens, primary_exposure, fix_priority, bands,
        profile_shape, lens_details, score_basis, limitations, next_step.
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
                "We need all three lens scores (AEO, GEO, AAX) for a full picture. "
                "Re-run the crawl or check for scoring errors."
            ),
            "weakest_lens": None,
            "strongest_lens": None,
            "primary_exposure": None,
            "fix_priority": None,
            "bands": {},
            "profile_shape": "incomplete",
            "lens_details": {},
            "score_basis": score_basis,
            "limitations": (_AUTO_ONLY_LIMITATIONS if score_basis == "auto" else []),
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
            "meaning": _band_meaning(band),
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
        "score_basis": score_basis,
        "limitations": (_AUTO_ONLY_LIMITATIONS if score_basis == "auto" else []),
        "next_step": _NEXT_STEP,
    }  # noqa: E501
