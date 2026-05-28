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
# ---------------------------------------------------------------------------

_BAND_THRESHOLDS: list[tuple[float, float, BandName, str]] = [
    (0, 39, "broken", "AI can't work with this yet."),
    (
        40,
        54,
        "weak",
        "Important pieces are missing or unclear.",
    ),
    (
        55,
        69,
        "developing",
        "The basics are there. Key gaps remain.",
    ),
    (
        70,
        84,
        "strong",
        "Solid baseline. Most signals are in place.",
    ),
    (
        85,
        100,
        "excellent",
        "Strong across all automated checks.",
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


# ---------------------------------------------------------------------------
# Lens-aware label templates
# ---------------------------------------------------------------------------

_LENS_META: dict[LensName, dict[str, str]] = {
    "AEO": {
        "gap": "citation gap",
        "exposure": "answer structure",
        "failure": "answer-extraction failure",
        "critical_label": "AI can't find any answers",
        "primary_exposure": "AI won't quote you",
        "fix_priority": "Structured answers",
    },
    "GEO": {
        "gap": "recommendation gap",
        "exposure": "trust signals",
        "failure": "recommendation-signal failure",
        "critical_label": "Not enough trust",
        "primary_exposure": "AI won't recommend this website",
        "fix_priority": "Entity + trust signals",
    },
    "AAX": {
        "gap": "agent-readiness gap",
        "exposure": "agent experience",
        "failure": "agent-usability failure",
        "critical_label": "AI agents can't use the website",
        "primary_exposure": "Agents abandon the site before converting",
        "fix_priority": "Navigation + action paths",
    },
}

# ---------------------------------------------------------------------------
# Headline copy (per profile shape)
# ---------------------------------------------------------------------------

_HEADLINES: dict[str, str] = {
    "high_invisibility": ("AI systems are mostly guessing about this site"),
    "critical_failure": ("One thing is dragging everything else down"),
    "broken_in_strong_profile": ("The rest of the site works — one area doesn't"),
    "material_risk": ("AI can see the site, but doesn't trust what it finds"),
    "broad_exposure": ("One clear gap, plus a second area that's not ready"),
    "single_exposure": ("One area to fix. The rest holds up"),
    "partial_exposure": (
        "AI picks up pieces of the site, but can't get the full picture"
    ),
    "developing_with_strong": (
        "Almost there — {lens} is the one thing holding it back"
    ),
    "highly_readable": (
        "Strong automated baseline. Now verify the signals we can't measure"
    ),
    "strong_profile": ("Solid across the board. Next step: manual checks"),
    "needs_review": (
        "Unusual score pattern — review the factors before drawing conclusions"
    ),
    "incomplete": ("One or more scores could not be calculated"),
}

# ---------------------------------------------------------------------------
# Profile labels (per profile shape, with lens interpolation)
# ---------------------------------------------------------------------------

_PROFILE_LABELS: dict[str, str] = {
    "high_invisibility": "Invisible to AI",
    "critical_failure": "{critical_label}",
    "broken_in_strong_profile": "{critical_label}",
    "material_risk": "Multiple blind spots for AI",
    "broad_exposure": "Two areas need attention",
    "single_exposure": "{exposure} needs work",
    "partial_exposure": "Half-visible to AI",
    "developing_with_strong": "{exposure} needs work",
    "highly_readable": "AI reads this site well",
    "strong_profile": "Strong foundation for AI visibility",
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
        "AI systems can't reliably quote, recommend, or "
        "act on this site. The information might be there, "
        "but it's not structured in a way AI can use."
    ),
    # Rule 2a — lens-specific
    "critical_failure": {
        "AEO": (
            "The content is probably good, but AI can't "
            "pull clean answers from it. Without structured "
            "answers, your best content gets paraphrased into "
            "generic summaries — or skipped entirely."
        ),
        "GEO": (
            "When someone asks AI for a recommendation in "
            "your space, your brand won't come up. The trust "
            "signals AI needs — entity data, authority "
            "markers, social proof — aren't strong enough yet."
        ),
        "AAX": (
            "AI agents land on this site and get stuck. They "
            "can't figure out the offer, find the next step, "
            "or complete a task. That means lost conversions "
            "from agent-mediated buyers."
        ),
    },
    # Rule 2b — lens-specific
    "broken_in_strong_profile": {
        "AEO": (
            "AI agents can use the site and trust signals "
            "are decent, but AI can't extract clean answers. "
            "This one gap means your content gets flattened "
            "or ignored in AI-generated responses."
        ),
        "GEO": (
            "Answers are extractable and agents can navigate, "
            "but AI doesn't see enough authority or trust "
            "signals to recommend the brand. Competitors with "
            "clearer entity data will get picked instead."
        ),
        "AAX": (
            "Answers and trust signals work, but AI agents "
            "hit friction trying to complete tasks. If an "
            "agent can't finish the journey, agent-mediated "
            "buyers drop off before converting."
        ),
    },
    # Rule 3 — no lens variant
    "material_risk": (
        "AI picks up fragments of the site but can't build "
        "a complete picture. Important content, trust signals, "
        "or agent paths are missing or unclear in more than "
        "one area."
    ),
    # Rule 4 — lens-specific
    "broad_exposure": {
        "AEO": (
            "AI struggles to extract answers, and a second "
            "area is only partially ready. The site isn't "
            "giving AI enough to work with in multiple places."
        ),
        "GEO": (
            "Trust and authority signals are weak, and "
            "another area is still developing. AI doesn't "
            "have enough reason to recommend this brand over "
            "alternatives."
        ),
        "AAX": (
            "AI agents face friction on this site, and a "
            "second area isn't fully ready either. Agent-"
            "mediated buyers are unlikely to complete their "
            "journey here."
        ),
    },
    # Rule 5 — lens-specific
    "single_exposure": {
        "AEO": (
            "Trust signals and agent experience are in good "
            "shape, but AI can't consistently pull structured "
            "answers from the content. Fix the answer "
            "structure and the overall picture improves fast."
        ),
        "GEO": (
            "Answers are extractable and agents can navigate, "
            "but AI doesn't see enough trust signals to "
            "recommend the brand. Entity data, author info, "
            "and social proof need work."
        ),
        "AAX": (
            "Answers and trust signals work, but AI agents "
            "struggle to complete tasks on this site. "
            "Navigation, CTAs, or action paths need to be "
            "clearer for agents to follow through."
        ),
    },
    # Rule 6 — no lens variant
    "partial_exposure": (
        "AI can read parts of the site, but the overall "
        "picture is incomplete. Multiple areas are partially "
        "there — close, but not enough for AI to act on "
        "with confidence."
    ),
    # Rule 7 — lens-specific
    "developing_with_strong": {
        "AEO": (
            "Trust signals and agent experience are strong. "
            "The gap is answer structure — AI can't "
            "consistently extract the best content yet. "
            "This is the one area holding the score back."
        ),
        "GEO": (
            "Answers are extractable and agents work well. "
            "The gap is trust: authority, entity, and social "
            "proof signals need to be stronger for AI to "
            "recommend the brand."
        ),
        "AAX": (
            "Answers and trust signals are strong. The gap "
            "is agent experience — AI agents need clearer "
            "guidance, navigation, or action paths to "
            "complete useful tasks."
        ),
    },
    # Rule 8 — no lens variant
    "highly_readable": (
        "AI reads the site well across citation, "
        "recommendation, and agent usability. The automated "
        "baseline is strong — the next step is verifying "
        "the signals this scan can't measure."
    ),
    # Rule 9 — no lens variant
    "strong_profile": (
        "Strong baseline across all three areas. This scan "
        "doesn't cover citation accuracy, competitive "
        "positioning, or capture rate — those need manual "
        "checks."
    ),
    # Rule 10 — no lens variant
    "needs_review": (
        "The score pattern is unusual and doesn't match "
        "common profiles. Look at the individual factors "
        "before drawing conclusions."
    ),
    # Incomplete — no lens variant
    "incomplete": (
        "All three scores (AEO, GEO, AAX) are needed for "
        "a complete read. Re-run the analysis or check for "
        "scoring errors."
    ),
}

# ---------------------------------------------------------------------------
# Limitations for auto-only scans (what the free scan doesn't show)
# ---------------------------------------------------------------------------

_AUTO_ONLY_LIMITATIONS: list[str] = [
    "Actual citation frequency (requires manual citation audit)",
    "Query-match relevance (requires query-match analysis)",
    "Competitor comparison (requires competitive analysis)",
    "Voice assistant presence (requires voice testing)",
    "Capture rate accuracy (requires Search Console verification)",
]

# ---------------------------------------------------------------------------
# Next step recommendation (always present)
# ---------------------------------------------------------------------------

_NEXT_STEP: str = "Review the missing manual checks before treating this as final."


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
        aeo_score: AEO composite score (0–100) or None.
        geo_score: GEO composite score (0–100) or None.
        aax_score: AAX composite score (0–100) or None.
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
            "headline": ("One or more scores could not be calculated."),
            "diagnosis": (
                "The interpretation matrix requires all three "
                "lens scores (AEO, GEO, AAX). Re-run the crawl "
                "or check for scoring errors."
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
    scores: dict[LensName, float] = {
        "AEO": aeo_score,
        "GEO": geo_score,
        "AAX": aax_score,
    }  # type: ignore[assignment]
    bands: dict[LensName, BandName] = {}
    for lens, score in scores.items():
        bands[lens] = _band_for(score)

    # Band counts
    band_counts: dict[BandName, int] = {
        "broken": 0,
        "weak": 0,
        "developing": 0,
        "strong": 0,
        "excellent": 0,
    }
    for band in bands.values():
        band_counts[band] += 1

    broken_count = band_counts["broken"]
    weak_count = band_counts["weak"]
    developing_count = band_counts["developing"]
    strong_count = band_counts["strong"]
    excellent_count = band_counts["excellent"]
    strong_or_better_count = strong_count + excellent_count

    avg = sum(scores.values()) / 3.0

    # Weakest and strongest lenses
    weakest_lens: LensName = min(scores, key=lambda k: scores[k])
    strongest_lens: LensName = max(scores, key=lambda k: scores[k])

    # Lens metadata
    lens_meta = _LENS_META[weakest_lens]

    # ------------------------------------------------------------------
    # Profile shape decision table (first match wins)
    # ------------------------------------------------------------------
    profile_shape: ProfileShape
    tone: Tone
    profile_label: str

    # Rule 1
    if broken_count >= 2 or avg < 45:
        profile_shape = "high_invisibility"
        tone = "critical"
        profile_label = _PROFILE_LABELS["high_invisibility"]

    # Rule 2a — broken lens, avg < 65
    elif broken_count == 1 and avg < 65:
        profile_shape = "critical_failure"
        tone = "critical"
        profile_label = _resolve_profile_label("critical_failure", lens_meta)

    # Rule 2b — broken lens, avg >= 65
    elif broken_count == 1 and avg >= 65:
        profile_shape = "broken_in_strong_profile"
        tone = "serious"
        profile_label = _resolve_profile_label("broken_in_strong_profile", lens_meta)

    # Rule 3
    elif weak_count + broken_count >= 2:
        profile_shape = "material_risk"
        tone = "serious"
        profile_label = _PROFILE_LABELS["material_risk"]

    # Rule 4
    elif weak_count + broken_count == 1 and developing_count >= 1:
        profile_shape = "broad_exposure"
        tone = "serious"
        profile_label = _resolve_profile_label("broad_exposure", lens_meta)

    # Rule 5
    elif weak_count + broken_count == 1:
        profile_shape = "single_exposure"
        tone = "moderate"
        profile_label = _resolve_profile_label("single_exposure", lens_meta)

    # Rule 6
    elif developing_count >= 2:
        profile_shape = "partial_exposure"
        tone = "moderate"
        profile_label = _PROFILE_LABELS["partial_exposure"]

    # Rule 7
    elif developing_count == 1 and strong_or_better_count >= 2:
        profile_shape = "developing_with_strong"
        tone = "limited"
        profile_label = _resolve_profile_label("developing_with_strong", lens_meta)

    # Rule 8
    elif excellent_count == 3:
        profile_shape = "highly_readable"
        tone = "positive"
        profile_label = _PROFILE_LABELS["highly_readable"]

    # Rule 9
    elif strong_or_better_count == 3:
        profile_shape = "strong_profile"
        tone = "positive"
        profile_label = _PROFILE_LABELS["strong_profile"]

    # Rule 10 — fallback
    else:
        profile_shape = "needs_review"
        tone = "moderate"
        profile_label = _PROFILE_LABELS["needs_review"]

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
        band = bands[lens]  # type: ignore[assignment]
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
