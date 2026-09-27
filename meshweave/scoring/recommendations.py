"""Recommendation generator from scoring raw data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meshweave.ai.models import (
    CONTACT_PAGE_POINTS,
    CONTACT_POINT_SCHEMA_POINTS,
    LEGAL_ONLY_EMAIL_PENALTY,
    LISTED_EMAIL_POINTS,
    MAILTO_POINTS,
    OBFUSCATED_EMAIL_PENALTY,
    SAME_DOMAIN_EMAIL_CAP,
    SAME_DOMAIN_EMAIL_PENALTY,
    SAME_DOMAIN_EMAIL_POINTS,
    SOCIAL_LINK_POINTS,
    THIRD_PARTY_EMAIL_POINTS,
)
from meshweave.scoring import aax_fields, geo
from meshweave.scoring.composite import LENS_PUBLIC_LABELS, expected_lens_delta

# Map factor keys to their pillar (aeo, geo, aax)
_FACTOR_TO_PILLAR: dict[str, str] = {
    # AEO factors
    "answerability": "aeo",
    "schema": "aeo",
    "content_structure": "aeo",
    "freshness": "aeo",
    # GEO factors
    "topical_authority": "geo",
    "eeat": "geo",
    "crawl_access": "geo",
    "content_depth": "geo",
    "entity_consistency": "geo",
    # AAX factors
    "homepage_comprehension": "aax",
    "meta_optimization": "aax",
    "content_delta": "aax",
    "email_validation": "aax",
    "contactability": "aax",
}


def generate_recommendations(
    aeo_factors: dict[str, dict],
    geo_factors: dict[str, dict],
    payload: dict[str, Any] | None = None,
    aax_factors: dict[str, dict] | None = None,
    contactability: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate actionable recommendations based on factor scores.

    Each recommendation carries ``expected_points``: the model-derived
    composite delta the lens score moves by when the fix lands, computed
    with the same weights, renormalization, and calibration curve as the
    real score. Recommendations are sorted by that number so the fix
    order is the model's own ranking, not a typed-in estimate.

    Args:
        aeo_factors: AEO factor score dicts.
        geo_factors: GEO factor score dicts.
        payload: Optional crawl payload for audit.meta data
            (canonical_issues, duplicate_og_titles, etc.).
        aax_factors: Optional AAX factor score dicts.
        contactability: Optional contactability signal dict
            (``aax.contactability`` from the AAX analysis). Passed
            explicitly because ``payload["scores"]`` is not yet set
            when recommendations are first generated.

    Returns:
        List of recommendation dicts sorted by expected impact.
    """
    lens_factors = {
        "aeo": aeo_factors,
        "geo": geo_factors,
        "aax": aax_factors or {},
    }

    recs: list[dict[str, Any]] = []
    recs.extend(_aeo_recommendations(aeo_factors))
    recs.extend(_geo_recommendations(geo_factors))
    if payload:
        recs.extend(_payload_recommendations(payload, aeo_factors))
    if aax_factors:
        recs.extend(_aax_recommendations(aax_factors))
    recs.extend(_contactability_recommendations(contactability, payload))

    for rec in recs:
        rec["pillar"] = _FACTOR_TO_PILLAR.get(rec.get("factor", ""), "aeo")
        rec.setdefault("guidance", _get_guidance(rec["title"]))
        _attach_expected_points(rec, lens_factors)
        rec["priority"] = priority_for_points(rec["expected_points"])

    recs = [r for r in recs if not _is_non_fix(r)]
    _sort_recommendations(recs)

    return recs


# Expected-points thresholds for the priority bands: priority is derived
# from the predicted lens delta, never typed in per fix.
HIGH_PRIORITY_POINTS = 3.0
MEDIUM_PRIORITY_POINTS = 1.0


def priority_for_points(points: float | None) -> str:
    """Priority band for a fix's expected points (None ranks low)."""
    if points is None:
        return "low"
    if points >= HIGH_PRIORITY_POINTS:
        return "high"
    if points >= MEDIUM_PRIORITY_POINTS:
        return "medium"
    return "low"


def _is_non_fix(rec: dict[str, Any]) -> bool:
    """True for a predicted fix that moves the score by nothing.

    Fixes without a factor-scale counterfactual (``expected_points``
    None, e.g. canonical hygiene) stay and carry their own explanation.
    """
    points = rec["expected_points"]
    return points is not None and points <= 0


def _attach_expected_points(
    rec: dict[str, Any],
    lens_factors: dict[str, dict[str, dict]],
) -> None:
    """Compute expected_points and render the impact string in place.

    Recs declare ``_target_score`` (the factor score after the fix) via
    the ``target`` helper. Recs without one — hygiene issues outside the
    factor scales — keep ``expected_points: None`` and their qualitative
    impact text.
    """
    target = rec.pop("_target_score", None)
    if target is None:
        rec["expected_points"] = None
        return
    lens = rec["pillar"]
    factors = lens_factors.get(lens) or {}
    delta = expected_lens_delta(lens, factors, rec["factor"], target)
    rec["expected_points"] = delta
    if delta is not None:
        rec["impact"] = f"{LENS_PUBLIC_LABELS[lens]} +{delta:.1f} points"


def _sort_recommendations(recs: list[dict[str, Any]]) -> None:
    """Sort by expected points, highest first; unpredicted fixes last."""

    def sort_key(r: dict[str, Any]) -> tuple[int, float]:
        pts = r["expected_points"]
        return (1, 0.0) if pts is None else (0, -pts)

    recs.sort(key=sort_key)


def target(current: float | None, gain: float) -> float:
    """Factor score after the fix: current plus the additive gain, capped.

    ``current`` is the factor's present score (None when the factor is
    not yet measured — e.g. the page-scope crawl_access placeholder);
    the fix introduces it at its additive value.
    """
    base = current if current is not None else 0.0
    return min(100.0, base + gain)


def _factor_score(factors: dict[str, dict], key: str) -> float | None:
    """Current numeric score for a factor, or None."""
    return (factors.get(key) or {}).get("score")


def _factor_raw(factors: dict[str, dict], key: str) -> dict:
    """Raw data dict for a factor, defaulted to empty."""
    return (factors.get(key) or {}).get("raw") or {}


def _aeo_recommendations(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for AEO factors (answerability, schema, structure)."""
    recs: list[dict[str, Any]] = []
    recs.extend(_answerability_recs(aeo_factors))
    recs.extend(_schema_coverage_recs(aeo_factors))
    recs.extend(_content_structure_recs(aeo_factors))
    return recs


# Finding titles per benchmark question. Unsupported and contradictory
# answers become findings in the existing shape and flow through
# expected_points like any other finding.
_ANSWERABILITY_FINDING_TITLES: dict[str, str] = {
    "offer": "Missing offer",
    "audience": "Missing audience",
    "use_case": "Missing use case",
    "differentiation": "Unsubstantiated claim",
    "scope": "Absent constraint",
    "next_step": "Next step not locatable",
}

_ANSWERABILITY_FAILING_VERDICTS = ("unsupported", "contradictory")


def _answerability_recs(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """One finding per unsupported or contradictory benchmark answer."""
    questions = _factor_raw(aeo_factors, "answerability").get("questions") or []
    applicable = [q for q in questions if _answerable_question(q)]
    failing = [
        q for q in applicable if q.get("verdict") in _ANSWERABILITY_FAILING_VERDICTS
    ]
    if not failing:
        return []
    # Fixing one failing question moves it from 0 to full question points
    # on the factor's mean scale.
    gain = 100.0 / len(applicable)
    current = _factor_score(aeo_factors, "answerability")
    return [
        {
            "factor": "answerability",
            "title": _ANSWERABILITY_FINDING_TITLES.get(
                str(q.get("question_id")), "Answerability gap"
            ),
            "detail": _answerability_finding_detail(q),
            "impact": "Answerable +2-6 points estimated",
            "_target_score": target(current, gain),
        }
        for q in failing
    ]


def _answerable_question(question: dict) -> bool:
    """True when the question record counts toward findings."""
    return not question.get("error") and question.get("verdict") != "not_applicable"


def _answerability_finding_detail(question: dict) -> str:
    """Finding detail naming the failure and the missing facts."""
    text = str(question.get("question", ""))
    if question.get("verdict") == "contradictory":
        lead = f"The crawled pages make conflicting claims about: {text}"
    else:
        lead = f"The crawled pages cannot support an answer to: {text}"
    missing = [str(f) for f in (question.get("missing_facts") or []) if f]
    if missing:
        return f"{lead} Missing: {'; '.join(missing[:3])}."
    return f"{lead}."


def _schema_coverage_recs(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Schema-coverage and FAQ-schema recommendations."""
    if "schema" not in aeo_factors:
        return []
    recs: list[dict[str, Any]] = []
    schema_raw = _factor_raw(aeo_factors, "schema")
    coverage_pct = schema_raw.get("coverage_pct") or 0
    has_faq = schema_raw.get("has_faq_schema", False)
    current = _factor_score(aeo_factors, "schema")

    if coverage_pct < 50:
        recs.append(
            {
                "factor": "schema",
                "title": "Add structured data (JSON-LD) to more pages",
                "detail": (
                    f"Only {coverage_pct:.0f}% of pages have schema markup. "
                    "Add FAQPage, HowTo, or Article schema to key pages."
                ),
                "impact": "Answerable +8-15 points estimated",
                # Coverage percentage is the factor's base score; a solid
                # fix reaches 80% coverage.
                "_target_score": 80.0,
            }
        )

    if not has_faq:
        recs.append(
            {
                "factor": "schema",
                "title": "Add FAQPage schema to key pages",
                "detail": (
                    "No FAQPage schema found. Add FAQ sections with 40-60 word "
                    "answers to product, pricing, and how-it-works pages."
                ),
                "impact": "Answerable +2-4 points estimated",
                # FAQPage bonus: +10 in the factor's additive scale.
                "_target_score": target(current, 10.0),
            }
        )

    return recs


def _content_structure_recs(aeo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Site-average structure and thin-page recommendations."""
    recs: list[dict[str, Any]] = []
    content_raw = _factor_raw(aeo_factors, "content_structure")
    site_avg = content_raw.get("site_average") or 0
    pages_evaluated = content_raw.get("pages_evaluated") or 0

    # Thresholds track the interpretation bands: a site-average below 55
    # sits in the "weak" band (40-59) or lower and needs the general
    # structure recommendation.
    if site_avg < 55 and pages_evaluated > 0:
        recs.append(
            {
                "factor": "content_structure",
                "title": "Improve content structure across pages",
                "detail": (
                    f"Average content structure score is {site_avg:.0f}/100 "
                    f"across {pages_evaluated} pages. Add headings (H1-H6), "
                    "lists, tables, and ensure 300+ words per page."
                ),
                "impact": "Answerable +5-10 points estimated",
                # Band threshold: lift the site average out of "weak".
                "_target_score": 70.0,
            }
        )

    recs.extend(_thin_pages_recs(aeo_factors, content_raw))
    return recs


def _thin_pages_recs(
    aeo_factors: dict[str, dict], content_raw: dict
) -> list[dict[str, Any]]:
    """Recommendation for pages scoring below the weak-band boundary (40)."""
    # Thin pages — below the "broken"/"weak" band boundary (40)
    per_page = content_raw.get("per_page_scores") or {}
    thin_pages = [u for u, s in per_page.items() if s < 40]
    if not thin_pages:
        return []
    examples = ", ".join(thin_pages[:3])
    # Lifting each thin page to the strong-band boundary (70) raises the
    # site average proportionally to the share of thin pages.
    fixed_avg = _site_average_after_fix(per_page, thin_pages, 70.0)
    return [
        {
            "factor": "content_structure",
            "title": f"Enrich {len(thin_pages)} thin page(s)",
            "detail": (
                f"Pages with low structure scores: {examples}. "
                "Add headings, content, images with alt text."
            ),
            "impact": "Answerable +2-6 points estimated",
            "_target_score": fixed_avg,
        }
    ]


def _site_average_after_fix(
    per_page: dict[str, float],
    fixed_urls: list[str],
    fixed_score: float,
) -> float:
    """Site-average structure score after lifting the fixed pages."""
    if not per_page:
        return 0.0
    total = sum(fixed_score if u in fixed_urls else s for u, s in per_page.items())
    return min(100.0, total / len(per_page))


def _geo_recommendations(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for the Reachable factors."""
    recs: list[dict[str, Any]] = []
    recs.extend(_entity_consistency_recs(geo_factors))
    recs.extend(_eeat_recs(geo_factors))
    recs.extend(_crawl_access_recommendations(geo_factors))
    recs.extend(_content_depth_rec(geo_factors))
    recs.extend(_topical_authority_rec(geo_factors))
    return recs


# Points each consistency signal adds on the 0-100 entity scale.
_ENTITY_TOTAL = geo.ENTITY_NAME_POINTS + geo.ENTITY_DESCRIPTION_POINTS
_ENTITY_NAME_GAIN = geo.ENTITY_NAME_POINTS * 100.0 / _ENTITY_TOTAL
_ENTITY_DESCRIPTION_GAIN = geo.ENTITY_DESCRIPTION_POINTS * 100.0 / _ENTITY_TOTAL


def _entity_consistency_recs(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Fixes for an organization name or description that varies by page."""
    if "entity_consistency" not in geo_factors:
        return []
    raw = _factor_raw(geo_factors, "entity_consistency")
    current = _factor_score(geo_factors, "entity_consistency")
    recs: list[dict[str, Any]] = []
    if not raw.get("name_consistent"):
        recs.append(
            {
                "factor": "entity_consistency",
                "title": "Use one organization name on every page",
                "detail": _variants_detail("name", raw.get("name_variants")),
                "impact": "",
                "_target_score": target(current, _ENTITY_NAME_GAIN),
            }
        )
    if not raw.get("desc_consistent"):
        recs.append(
            {
                "factor": "entity_consistency",
                "title": "Use one organization description on every page",
                "detail": _variants_detail("description", raw.get("desc_variants")),
                "impact": "",
                "_target_score": target(current, _ENTITY_DESCRIPTION_GAIN),
            }
        )
    return recs


def _variants_detail(label: str, variants: Any) -> str:
    """Detail naming the differing organization name/description values."""
    found = [str(v) for v in (variants or []) if v]
    if not found:
        return (
            f"No consistent organization {label} was found across the "
            "crawled pages. State the same one in each page's "
            "Organization JSON-LD."
        )
    quoted = "; ".join(f'"{v}"' for v in found[:3])
    return (
        f"The crawled pages use {len(found)} different organization "
        f"{label}s: {quoted}. Pick one and use it on every page."
    )


def _eeat_recs(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Fixes for each missing site-side trust signal."""
    if "eeat" not in geo_factors:
        return []
    raw = _factor_raw(geo_factors, "eeat")
    current = _factor_score(geo_factors, "eeat")
    return [
        {
            "factor": "eeat",
            "title": title,
            "detail": detail,
            "impact": "",
            "_target_score": target(current, gain),
        }
        for key, title, detail, gain in _EEAT_FIXES
        if not raw.get(key)
    ]


# (raw flag, title, detail, points on the trust-signal scale)
_EEAT_FIXES: tuple[tuple[str, str, str, int], ...] = (
    (
        "has_org_schema",
        "Add Organization JSON-LD schema",
        "No Organization schema found. Add an Organization block to your "
        "homepage with name, logo, and url.",
        geo.ORG_POINTS,
    ),
    (
        "has_author_info",
        "Add author information to articles",
        "No author schema found in articles. Add author JSON-LD to "
        "article pages with name and url.",
        geo.AUTHOR_POINTS,
    ),
    (
        "has_contact",
        "Publish a contact page",
        "No contact page or ContactPoint schema was found in the crawl. "
        "Add a /contact page that says how to reach you.",
        geo.CONTACT_POINTS,
    ),
    (
        "has_privacy",
        "Publish privacy and terms pages",
        "No privacy, terms, or legal page was found in the crawl. Link "
        "them from every page footer.",
        geo.POLICY_POINTS,
    ),
)


def _crawl_access_recommendations(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Crawl-accessibility recommendations (note-based or raw-based)."""
    crawl_access = geo_factors.get("crawl_access") or {}
    crawl_note = crawl_access.get("note")
    crawl_score = crawl_access.get("score")

    if crawl_score is None and crawl_note:
        return [
            {
                "factor": "crawl_access",
                "title": "Re-analyze as domain for accessibility score",
                "detail": crawl_note,
                "impact": "",
                # Introducing the factor at a typical domain-crawl value
                # (46 additive points) when the fix is "run the domain crawl".
                "_target_score": target(crawl_score, geo.crawl_access_scale(46)),
            }
        ]
    if crawl_access.get("raw"):
        return _crawl_access_recs(crawl_access)
    return []


def _content_depth_rec(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Fix naming the shallow pages when content depth is weak."""
    current = _factor_score(geo_factors, "content_depth")
    raw = _factor_raw(geo_factors, "content_depth")
    page_words: dict[str, int] = raw.get("page_words") or {}
    shallow = sorted(
        (u for u, w in page_words.items() if w < _DEPTH_TARGET_WORDS),
        key=lambda u: page_words[u],
    )
    if not _is_weak(current) or not shallow:
        return []
    examples = ", ".join(f"{u} ({page_words[u]} words)" for u in shallow[:3])
    expanded = [max(w, _DEPTH_TARGET_WORDS) for w in page_words.values()]
    metrics = (raw["pages_with_code"], raw["pages_with_tables"])
    return [
        {
            "factor": "content_depth",
            "title": f"Expand {len(shallow)} page(s) under 1,000 words",
            "detail": (
                f"Content depth scores {current:.0f}/100. Shallowest pages: "
                f"{examples}. Expand them with the specifics a buyer needs: "
                "how it works, examples, and limits."
            ),
            "impact": "",
            "_target_score": geo.content_depth_score(expanded, metrics),
        }
    ]


# Factors below this score get a fix even when no specific signal is missing.
_WEAK_FACTOR_SCORE = 60.0


def _is_weak(score: float | None) -> bool:
    """True when a measured factor sits below the weak-factor threshold."""
    return score is not None and score < _WEAK_FACTOR_SCORE


# Word count at which a page counts as in-depth in the content-depth score.
_DEPTH_TARGET_WORDS = 1000


def _topical_authority_rec(geo_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Fix for partial schema coverage when topical coverage is weak."""
    current = _factor_score(geo_factors, "topical_authority")
    raw = _factor_raw(geo_factors, "topical_authority")
    coverage = raw.get("coverage_pct") or 0
    if not _is_weak(current) or coverage >= 100:
        return []
    fixed = geo.topical_authority_score(
        100.0,
        raw.get("schema_types_count") or 0,
        bool(raw.get("name_consistent")),
        bool(raw.get("desc_consistent")),
        raw.get("content_page_ratio") or 0.0,
    )
    return [
        {
            "factor": "topical_authority",
            "title": "Add structured data to every crawled page",
            "detail": (
                f"Only {coverage:.0f}% of crawled pages carry JSON-LD, using "
                f"{raw.get('schema_types_count') or 0} schema type(s). Describe "
                "each page's subject in JSON-LD."
            ),
            "impact": "",
            "_target_score": fixed,
        }
    ]


def _crawl_access_recs(crawl_access: dict) -> list[dict[str, Any]]:
    """One fix per missing crawl-access signal, on the 0-100 scale."""
    raw = crawl_access["raw"]
    current = crawl_access.get("score")
    recs = [
        {
            "factor": "crawl_access",
            "title": title,
            "detail": detail,
            "impact": "",
            "_target_score": target(current, geo.crawl_access_scale(points)),
        }
        for present, title, detail, points in _crawl_access_fixes(raw)
        if not present
    ]
    recs.extend(_bot_access_recs(raw, current))
    return recs


def _crawl_access_fixes(raw: dict) -> list[tuple[bool, str, str, int]]:
    """(present, title, detail, additive points) per crawl-access file."""
    return [
        (
            bool(raw.get("llms_txt_exists")),
            "Publish an llms.txt file",
            "No llms.txt found. Create /llms.txt with a brief site description "
            "and links to your key pages.",
            geo.LLMS_TXT_POINTS,
        ),
        (
            bool(raw.get("llms_full_txt_exists")),
            "Publish an llms-full.txt file",
            "No llms-full.txt found. Publish the full text of your key pages "
            "as /llms-full.txt.",
            geo.LLMS_FULL_TXT_POINTS,
        ),
        (
            bool(raw.get("robots_exists")),
            "Add a robots.txt file",
            "No robots.txt found. Create one at the domain root to guide AI "
            "crawlers and search engines.",
            geo.ROBOTS_POINTS,
        ),
        (
            bool(raw.get("sitemap_count")),
            "Declare your sitemap in robots.txt",
            "robots.txt declares no sitemap. Add a Sitemap: line pointing to "
            "your XML sitemap.",
            geo.SITEMAP_POINTS,
        ),
    ]


def _bot_access_recs(raw: dict, current: float | None) -> list[dict[str, Any]]:
    """Fix naming the AI crawlers robots.txt does not fully allow."""
    statuses = raw.get("bot_statuses") or {}
    missing = {
        name: full - geo.bot_points(name, statuses.get(name))
        for name, full in geo.AI_BOT_POINTS.items()
    }
    blocked = [name for name, pts in missing.items() if pts > 0]
    if not blocked:
        return []
    listed = ", ".join(f"{n} ({statuses.get(n) or 'not allowed'})" for n in blocked)
    return [
        {
            "factor": "crawl_access",
            "title": "Allow AI crawlers in robots.txt",
            "detail": f"robots.txt does not fully allow: {listed}.",
            "impact": "",
            "_target_score": target(
                current, geo.crawl_access_scale(sum(missing.values()))
            ),
        }
    ]


def _payload_recommendations(
    payload: dict[str, Any],
    aeo_factors: dict[str, dict],
) -> list[dict[str, Any]]:
    """Recommendations driven by the crawl payload audit.meta data."""
    recs: list[dict[str, Any]] = []
    meta = (payload.get("audit") or {}).get("meta") or {}

    # Canonical issues
    canonical_issues = meta.get("canonical_issues") or []
    if canonical_issues:
        recs.append(
            {
                "factor": "schema",
                "title": f"Fix canonical URL mismatches on {len(canonical_issues)} page(s)",
                "detail": (
                    "These pages declare a canonical URL that differs from "
                    f"the crawled URL: {_canonical_examples(canonical_issues)}. "
                    "Crawlers cannot tell which URL is the real one."
                ),
                "impact": _UNSCORED_IMPACT,
            }
        )

    # Duplicate OG titles
    dup_titles = meta.get("duplicate_og_titles") or {}
    if dup_titles:
        recs.append(
            {
                "factor": "content_structure",
                "title": f"Differentiate OG titles — {len(dup_titles)} group(s) share the same title",
                "detail": (
                    "Multiple pages share identical OG titles. This "
                    "confuses social previews and page identity."
                ),
                "impact": _UNSCORED_IMPACT,
            }
        )

    recs.extend(_image_alt_recs(payload, aeo_factors))

    return recs


# Metadata hygiene sits outside every factor scale: no predicted points.
_UNSCORED_IMPACT = "Not scored: metadata hygiene, no points predicted"


def _canonical_examples(issues: list[Any]) -> str:
    """Up to three 'page → canonical' pairs from the canonical issues."""
    pairs = [
        f"{i.get('page')} → {i.get('canonical')}" if isinstance(i, dict) else str(i)
        for i in issues[:3]
    ]
    return "; ".join(pairs)


def _image_alt_recs(
    payload: dict[str, Any],
    aeo_factors: dict[str, dict],
) -> list[dict[str, Any]]:
    """Site-wide image alt-text recommendation."""
    total_imgs, imgs_with_alt = _site_image_counts(payload)
    if total_imgs <= 0 or (imgs_with_alt / total_imgs) >= 0.5:
        return []
    missing = total_imgs - imgs_with_alt
    # Alt coverage ≥80% earns the +10 alt points per affected page; the
    # site average rises by the pages' share of the +10.
    current = _factor_score(aeo_factors, "content_structure")
    fixed_avg = _alt_fix_target(aeo_factors, total_imgs, imgs_with_alt, current)
    return [
        {
            "factor": "content_structure",
            "title": f"Add alt text to {missing} image(s) across the site",
            "detail": (
                f"Only {imgs_with_alt}/{total_imgs} images have alt "
                "text. Add descriptive alt text to improve "
                "accessibility and image search visibility."
            ),
            "impact": "Answerable +3-5 points estimated",
            "_target_score": fixed_avg,
        }
    ]


def _alt_fix_target(
    aeo_factors: dict[str, dict],
    total_imgs: int,
    imgs_with_alt: int,
    current: float | None,
) -> float | None:
    """Content-structure target once alt coverage reaches 80%."""
    if current is None:
        return None
    per_page = (
        _factor_raw(aeo_factors, "content_structure").get("per_page_scores") or {}
    )
    if not per_page:
        return None
    # Pages missing the 10-point alt bonus are those below 80% coverage;
    # approximate the share by the global alt gap.
    alt_gap = 1.0 - (imgs_with_alt / total_imgs) if total_imgs else 0.0
    gain = 10.0 * alt_gap
    return min(100.0, current + gain)


def _site_image_counts(payload: dict[str, Any]) -> tuple[int, int]:
    """Total images and images with alt text across all markdown pages."""
    md_dict = payload.get("markdowns") or {}
    total_imgs = 0
    imgs_with_alt = 0
    if isinstance(md_dict, dict) and md_dict:
        for _url, pg in md_dict.items():
            if isinstance(pg, dict):
                cm = pg.get("content_metrics") or {}
                total_imgs += cm.get("images_total") or 0
                imgs_with_alt += cm.get("images_with_alt") or 0
    return total_imgs, imgs_with_alt


def _aax_recommendations(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendations for AAX factors."""
    recs: list[dict[str, Any]] = []
    recs.extend(_homepage_comprehension_rec(aax_factors))
    recs.extend(_content_delta_rec(aax_factors))
    recs.extend(_meta_optimization_rec(aax_factors))
    recs.extend(_email_validation_rec(aax_factors))
    return recs


def _homepage_comprehension_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for unclear homepage comprehension."""
    hc = aax_factors.get("homepage_comprehension")
    if not hc:
        return []
    hc_raw = hc.get("raw") or {}
    missing_fields = [
        _HOMEPAGE_FIELD_LABELS[k]
        for k in aax_fields.HOMEPAGE_FIELDS
        if not hc_raw.get(k)
    ]
    if not missing_fields:
        return []
    # Clarity/density gains are LLM verdicts, so predict from the missing
    # identity fields alone.
    per_field = aax_fields.HOMEPAGE_FIELD_WEIGHT * 100 / len(aax_fields.HOMEPAGE_FIELDS)
    return [
        {
            "factor": "homepage_comprehension",
            "title": "Improve homepage clarity for AI agents",
            "detail": (
                f"AI agents struggle to understand your site. "
                f"Missing: {', '.join(missing_fields)}. "
                "Make brand, product, audience, and CTA clear."
            ),
            "impact": "Actionable +8-15 points estimated",
            "_target_score": target(hc.get("score"), per_field * len(missing_fields)),
        }
    ]


_HOMEPAGE_FIELD_LABELS: dict[str, str] = {
    "brand": "brand",
    "product": "product",
    "target_audience": "audience",
    "call_to_action": "CTA",
}


def _content_delta_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Fixes quoting the buyer facts and essentials the pages leave out."""
    cd = aax_factors.get("content_delta")
    if not cd:
        return []
    raw = cd.get("raw") or {}
    return [*_missing_facts_rec(cd, raw), *_missing_essentials_rec(cd, raw)]


def _missing_facts_rec(cd: dict, raw: dict) -> list[dict[str, Any]]:
    """Fix quoting the missing buyer facts the content-delta test listed.

    Stating them moves the completeness verdict to "comprehensive"
    (30% of the factor); a site already rated comprehensive gains
    nothing from the fix and it is dropped as a non-fix.
    """
    facts = [str(w).strip() for w in (raw.get("weaknesses") or []) if str(w).strip()]
    if not facts:
        return []
    completeness = aax_fields.CONTENT_COMPLETENESS_MAP.get(
        raw.get("completeness", "incomplete"), 20
    )
    quoted = "; ".join(f'"{f}"' for f in facts[:3])
    return [
        {
            "factor": "content_delta",
            "title": f"State {len(facts)} missing buyer fact(s)",
            "detail": f"Buyers need these facts and the pages do not state them: {quoted}.",
            "guidance": f"Add to the relevant page: {quoted}.",
            "impact": "",
            "_target_score": target(
                cd.get("score"), aax_fields.COMPLETENESS_WEIGHT * (100 - completeness)
            ),
        }
    ]


def _missing_essentials_rec(cd: dict, raw: dict) -> list[dict[str, Any]]:
    """Fix naming the buyer essentials no crawled page states."""
    fields = aax_fields.richness_fields(raw)
    total = len(fields)
    missing = [k for k, ok in fields.items() if not ok]
    if not missing:
        return []
    names = ", ".join(missing)
    return [
        {
            "factor": "content_delta",
            "title": "State the missing essentials",
            "detail": f"No crawled page states the {names}.",
            "guidance": f"Say plainly on your key pages: {names}.",
            "impact": "",
            "_target_score": target(
                cd.get("score"), aax_fields.RICHNESS_WEIGHT * 100 * len(missing) / total
            ),
        }
    ]


def _meta_optimization_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Recommendation for weak/confusing metadata."""
    mo = aax_factors.get("meta_optimization")
    if not mo:
        return []
    mo_raw = mo.get("raw") or {}
    suggestions = _meta_suggestions(mo_raw)
    issues = _meta_issues(mo_raw)
    if not issues:
        return []
    if suggestions:
        detail = (
            "AI found specific metadata gaps: "
            + " ".join(suggestions[:3])
            + " Use clear, descriptive titles and meta tags for LLM "
            "understanding."
        )
    else:
        detail = (
            f"Improve: {', '.join(issues)}. Use clear, "
            "descriptive titles and meta tags for LLM "
            "understanding."
        )
    return [
        {
            "factor": "meta_optimization",
            "title": "Optimize metadata for AI crawlers",
            "detail": detail,
            "impact": "Actionable +2-5 points estimated",
            # Complete+clear+optimized+click is the perfect-verdict
            # factor score; predict the verdict-scale midpoint of the
            # weak fields (50 each) rather than assume perfection.
            "_target_score": target(mo.get("score"), 25.0),
        }
    ]


def _meta_suggestions(mo_raw: dict) -> list[str]:
    """Trimmed, non-empty improvement suggestions from the meta raw data."""
    return [
        s.strip()
        for s in (mo_raw.get("improvement_suggestions") or [])
        if s and s.strip()
    ]


def _meta_issues(mo_raw: dict) -> list[str]:
    """Issue labels for the metadata weaknesses found.

    Prefers the LLM's concrete ``improvement_suggestions`` when populated;
    falls back to weak-structure labels derived from the verdict fields.
    """
    suggestions = _meta_suggestions(mo_raw)
    if suggestions:
        return suggestions[:3]

    completeness = mo_raw.get("completeness", "minimal")
    clarity = mo_raw.get("clarity", "unclear")
    llm_opt = mo_raw.get("llm_optimization", "poor")
    would_click = mo_raw.get("would_click_through", False)

    if would_click and completeness != "minimal" and clarity != "unclear":
        return []
    issues: list[str] = []
    if not would_click:
        issues.append("value proposition")
    if completeness == "minimal":
        issues.append("metadata")
    if clarity == "unclear":
        issues.append("messaging")
    if llm_opt == "poor":
        issues.append("LLM-optimized descriptions")
    return issues


def _email_validation_rec(aax_factors: dict[str, dict]) -> list[dict[str, Any]]:
    """Fix for missing or low-confidence contact emails."""
    ev = aax_factors.get("email_validation")
    if not ev:
        return []
    ev_raw = ev.get("raw") or {}
    contacts = ev_raw.get("valid_contacts") or []
    if ev_raw.get("confidence", "low") != "low" and contacts:
        return _sales_contact_rec(ev, contacts)
    return [
        {
            "factor": "email_validation",
            "title": "Add valid contact emails for AI verification",
            "detail": (
                "No contact email could be confirmed as real. Add mailto: "
                "links with valid addresses on the contact page."
            ),
            "impact": "",
            # Predict a general contact at medium confidence.
            "_target_score": target(ev.get("score"), 50.0),
        }
    ]


def _sales_contact_rec(ev: dict, contacts: list[dict]) -> list[dict[str, Any]]:
    """Fix for a confirmed contact set that lacks a sales address."""
    types = {str(c.get("contact_type", "invalid")) for c in contacts}
    best = max(aax_fields.EMAIL_TYPE_SCORES.get(t, 0) for t in types)
    type_gain = aax_fields.EMAIL_TYPE_SCORES["sales"] - best
    if type_gain <= 0:
        return []
    # A second valid contact also earns the last 10 presence points.
    presence_gain = aax_fields.SECOND_CONTACT_POINTS if len(contacts) == 1 else 0.0
    listed = ", ".join(sorted(types))
    return [
        {
            "factor": "email_validation",
            "title": "Publish a sales contact address",
            "detail": (
                f"The confirmed contact emails are {listed} only. Add a "
                "dedicated sales address (for example sales@) with a "
                "mailto: link so buyers and agents know where to ask."
            ),
            "impact": "",
            "_target_score": target(ev.get("score"), type_gain + presence_gain),
        }
    ]


def _contactability_recommendations(
    contactability: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Fixes for each contactability penalty and the missing signals.

    Prefers the explicitly passed contactability signal; falls back to the
    payload only when the caller knows ``scores.aax`` is already present
    (e.g. re-scores loading a persisted payload).
    """
    contactability = _resolve_contactability(contactability, payload)
    if not contactability:
        return []
    recs = _contact_penalty_recs(contactability)
    recs.extend(_missing_signals_rec(contactability))
    return recs


def _missing_signals_rec(contactability: dict[str, Any]) -> list[dict[str, Any]]:
    """Fix for contact signals not covered by a penalty fix."""
    covered = _penalty_covered_signals(contactability)
    missing = {
        s: pts
        for s, pts in _CONTACT_SIGNAL_POINTS.items()
        if not contactability.get(s) and s not in covered
    }
    if not missing:
        return []
    labels = ", ".join(_CONTACT_SIGNAL_LABELS[s] for s in missing)
    return [
        {
            "factor": "contactability",
            "title": "Improve contactability for AI agents",
            "detail": (
                f"Missing: {labels}. AI agents need clear contact signals "
                "to identify a next step."
            ),
            "impact": "",
            "_target_score": _penalty_bound_target(
                contactability, None, sum(missing.values())
            ),
        }
    ]


# Additive point values of the contactability heuristic's signals.
_CONTACT_SIGNAL_POINTS: dict[str, int] = {
    "has_email": SAME_DOMAIN_EMAIL_POINTS,
    "has_mailto": MAILTO_POINTS,
    "has_contact_page": CONTACT_PAGE_POINTS,
    "has_social_links": SOCIAL_LINK_POINTS,
    "has_contact_point_schema": CONTACT_POINT_SCHEMA_POINTS,
}


@dataclass(frozen=True)
class _PenaltyFix:
    """Customer-facing fix for one contactability penalty."""

    title: str
    detail: str
    # Signal the fix also adds, whose points it earns alongside the penalty.
    signal: str
    signal_gain: int


_PENALTY_FIXES: dict[str, _PenaltyFix] = {
    SAME_DOMAIN_EMAIL_PENALTY: _PenaltyFix(
        title="Publish a contact email on your own domain",
        detail=(
            "No email address on the site uses the site's own domain, so "
            "contactability is capped at {cap} and the cap removes {lost} "
            "points. Publish an address on your own domain on the contact page."
        ),
        signal="has_email",
        signal_gain=SAME_DOMAIN_EMAIL_POINTS - THIRD_PARTY_EMAIL_POINTS,
    ),
    OBFUSCATED_EMAIL_PENALTY: _PenaltyFix(
        title="Make your email address clickable",
        detail=(
            "Every email address is obfuscated with no mailto: link, which "
            "removes {lost} points. Link at least one address with mailto:."
        ),
        signal="has_mailto",
        signal_gain=MAILTO_POINTS,
    ),
    LEGAL_ONLY_EMAIL_PENALTY: _PenaltyFix(
        title="Publish an email outside the legal pages",
        detail=(
            "Email addresses appear only on privacy or terms pages, which "
            "removes {lost} points. Put a contact address on the homepage "
            "or contact page."
        ),
        signal="",
        signal_gain=LISTED_EMAIL_POINTS,
    ),
}


def _contact_penalty_recs(contactability: dict[str, Any]) -> list[dict[str, Any]]:
    """One fix per applied penalty, naming it and the points it removes."""
    lost: dict[str, float] = contactability.get("penalty_points") or {}
    return [
        {
            "factor": "contactability",
            "title": fix.title,
            "detail": fix.detail.format(
                cap=SAME_DOMAIN_EMAIL_CAP,
                lost=f"{points:.0f}",
            ),
            "impact": "",
            "_target_score": _penalty_bound_target(
                contactability, reason, _penalty_gain(contactability, fix)
            ),
        }
        for reason, points in lost.items()
        if (fix := _PENALTY_FIXES.get(reason))
    ]


def _penalty_gain(contactability: dict[str, Any], fix: _PenaltyFix) -> float:
    """Signal points a penalty fix earns besides lifting the penalty."""
    if fix.signal == "has_email" and not contactability.get("has_email"):
        return _CONTACT_SIGNAL_POINTS["has_email"]
    return fix.signal_gain


def _penalty_covered_signals(contactability: dict[str, Any]) -> set[str]:
    """Signals a penalty fix already adds, excluded from the signals fix."""
    lost = contactability.get("penalty_points") or {}
    return {
        fix.signal
        for reason, fix in _PENALTY_FIXES.items()
        if reason in lost and fix.signal
    }


def _penalty_bound_target(
    contactability: dict[str, Any], lifted: str | None, gain: float
) -> float:
    """Contactability score after lifting one penalty and adding *gain*.

    Subtractive penalties other than *lifted* stay applied; the
    same-domain cap still binds unless it is the lifted penalty.
    """
    lost: dict[str, float] = contactability.get("penalty_points") or {}
    score = float(contactability.get("score") or 0.0)
    cap_lost = lost.get(SAME_DOMAIN_EMAIL_PENALTY)
    uncapped = score + (cap_lost or 0.0) + gain
    if lifted and lifted != SAME_DOMAIN_EMAIL_PENALTY:
        uncapped += lost[lifted]
    if cap_lost is not None and lifted != SAME_DOMAIN_EMAIL_PENALTY:
        uncapped = min(uncapped, float(SAME_DOMAIN_EMAIL_CAP))
    return min(100.0, uncapped)


def _resolve_contactability(
    contactability: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Explicit contactability signal, or the payload's persisted one."""
    if contactability:
        return contactability
    if not payload:
        return None
    scores = payload.get("scores") or {}
    aax_scores = scores.get("aax") or {}
    return aax_scores.get("contactability")


# Contact signal field → label used in the recommendation detail
_CONTACT_SIGNAL_LABELS: dict[str, str] = {
    "has_email": "email address",
    "has_mailto": "mailto: link",
    "has_contact_page": "contact page",
    "has_social_links": "social links",
    "has_contact_point_schema": "ContactPoint schema",
}


def _get_guidance(title: str) -> str:
    """Return guidance string for a recommendation, or empty string."""
    # Exact match first
    if title in _GUIDANCE:
        return _GUIDANCE[title]
    # Prefix match for dynamic titles
    for prefix, guidance in _GUIDANCE_PREFIX.items():
        if title.startswith(prefix):
            return guidance
    return ""


# Guidance strings keyed on exact recommendation title. Fixes that quote
# their own evidence set ``guidance`` directly.
_GUIDANCE: dict[str, str] = {
    "Missing offer": ("State plainly on the site what this company offers."),
    "Missing audience": ("Name who the offer is for, where a visitor can see it."),
    "Missing use case": (
        "Describe the core use case: why and when someone would use this."
    ),
    "Unsubstantiated claim": ("Back the claim with evidence on the site, or drop it."),
    "Absent constraint": (
        "State scope, constraints, and pricing: a price, a pricing "
        "model, or a clear route to get a quote."
    ),
    "Next step not locatable": (
        "Make the next step locatable: a clear start, buy, or contact path."
    ),
    "Add structured data (JSON-LD) to more pages": (
        "Add FAQPage, HowTo, or Article JSON-LD to key pages."
    ),
    "Add FAQPage schema to key pages": (
        "Add FAQ sections with short, direct answers to product and pricing pages."
    ),
    "Improve content structure across pages": (
        "Add headings, lists, and tables. Shoot for 300+ words per page."
    ),
    "Use one organization name on every page": (
        "Use the same organization name in every page's title and JSON-LD."
    ),
    "Use one organization description on every page": (
        "Use the same one-line organization description in every page's JSON-LD."
    ),
    "Add Organization JSON-LD schema": (
        "Drop an Organization block on your homepage with name, logo, and URL."
    ),
    "Add author information to articles": (
        "Add author JSON-LD with name and URL to article pages."
    ),
    "Publish a contact page": "Add a /contact page and link it from the footer.",
    "Publish privacy and terms pages": (
        "Publish /privacy and /terms and link them from the footer."
    ),
    "Publish an llms-full.txt file": (
        "Publish /llms-full.txt with the full text of your key pages."
    ),
    "Declare your sitemap in robots.txt": (
        "Add a Sitemap: line to robots.txt that points to your XML sitemap."
    ),
    "Allow AI crawlers in robots.txt": (
        "Allow the listed crawlers in robots.txt; keep private paths disallowed."
    ),
    "Add structured data to every crawled page": (
        "Add JSON-LD that names each page's subject: Service, Article, or FAQPage."
    ),
    "Publish a sales contact address": (
        "Add a sales@ address with a mailto: link to the contact page."
    ),
    "Publish a contact email on your own domain": (
        "Use an address on your own domain for the contact page and mailto: links."
    ),
    "Make your email address clickable": (
        "Wrap at least one contact address in a mailto: link."
    ),
    "Publish an email outside the legal pages": (
        "Put a contact address on the homepage or contact page."
    ),
    "Re-analyze as domain for accessibility score": (
        "Run the analysis at the domain root for a complete accessibility score."
    ),
    "Publish an llms.txt file": (
        "Create /.well-known/llms.txt with a short site description for AI crawlers."
    ),
    "Add a robots.txt file": (
        "Create /robots.txt to control which crawlers can access your site."
    ),
    "Optimize metadata for AI crawlers": (
        "Improve your value proposition, metadata, and descriptions for LLM understanding."
    ),
    "Add valid contact emails for AI verification": (
        "Add mailto: links with real addresses to your contact page."
    ),
    "Improve contactability for AI agents": (
        "Add the missing contact signals so AI agents can verify your "
        "business and act on it."
    ),
    "Improve homepage clarity for AI agents": (
        "Make your brand, product, audience, and call-to-action clear on the homepage."
    ),
}

# Prefix-based guidance for dynamic titles (contain counts, etc.).
_GUIDANCE_PREFIX: dict[str, str] = {
    "Enrich": "Add headings, content, and alt text to the listed pages.",
    "Fix canonical URL mismatches": (
        "Update the canonical tag on the listed page to match its actual URL."
    ),
    "Differentiate OG titles": (
        "Give each page a unique OG title that describes what's actually on it."
    ),
    "Add alt text to": "Add descriptive alt text to the listed images.",
    "Expand": (
        "Expand the listed pages with specifics: how it works, examples, limits."
    ),
}
