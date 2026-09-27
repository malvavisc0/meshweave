"""Public-copy sweep: customer-facing surfaces stay acronym-free and claim-free.

Phase-4/5 guard for the AI-friendly website rewrite. Scans every
customer-facing copy surface — rendered pages (template sources), exports
(report.md / diff.md renders, branded and unbranded), public and private
API JSON responses, JSON-LD builders (raw source), agent-facing
documentation, the site manifest, interpretation copy, nudge copy,
recommendation copy, and rating/implication bands — and asserts none of
them carries external-outcome claims or the internal AEO/GEO/AAX
acronyms.

Internal score-group keys (``aeo`` / ``geo`` / ``aax``), code identifiers,
and API field names are out of scope: only rendered or published copy
values are checked.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# webapp.db resolves its SQLite location at import time; tests own their data.
os.environ.setdefault("SQLITE_DIR", "/tmp/kilo/meshweave-test-db")
os.environ.setdefault("SQLITE_PATH", "/tmp/kilo/meshweave-test-db/webapp.sqlite3")

from fastapi_stub import load_api_v1_module

from meshweave.ai.models import (
    LEGAL_ONLY_EMAIL_PENALTY,
    OBFUSCATED_EMAIL_PENALTY,
    SAME_DOMAIN_EMAIL_PENALTY,
)
from meshweave.scoring.interpretation import interpret_profile
from meshweave.scoring.ratings import aax_rating, aeo_rating, geo_rating
from meshweave.scoring.recommendations import generate_recommendations
from webapp.services.nudges import _base_nudges
from webapp.utils.export import (
    build_export_context,
    render_diff_markdown,
    render_export_markdown,
)
from webapp.utils.scoring import score_implication

ROOT = Path(__file__).resolve().parent.parent

# Internal acronyms must never render. Case-sensitive word match: lowercase
# field names like ``aeo_score`` are code identifiers and stay.
ACRONYMS = re.compile(r"\b(?:AEO|GEO|AAX)\b")

# Retired vocabulary leaves public copy entirely (phrases + expansions).
RETIRED = re.compile(
    r"citation simulation|quotability|quotable|generative discovery"
    r"|recommendability|ai visibility"
    r"|answer engine optimization|generative engine optimization"
    r"|ai agent experience"
    r"|agent[- ]readability"
    r"|ai[- ]friendly",
    re.IGNORECASE,
)

# Standing vocabulary: words whose only use in our copy would be to rate a
# site's standing in outside systems. Ordinary nouns ("recommended fixes",
# "sources cited", "competitors") are not claims and are not listed.
STANDING = re.compile(r"\binvisib\w*|\bdominant\b", re.IGNORECASE)

# External-outcome promises: copy must never promise what outside systems
# (AI answers, search engines) will do for the business. Claims, not nouns.
PROMISE = re.compile(
    r"\brecommend(?:s|ed)? (?:you|your)\b"
    r"|\bget (?:you |your (?:site|brand|business) )?(?:cited|recommended|mentioned)\b"
    r"|\bbe (?:cited|recommended|mentioned)\b"
    r"|\brank(?:s|ing)? higher\b"
    r"|\bmore (?:traffic|visitors|leads|sales|conversions)\b"
    r"|\bincrease (?:your )?(?:traffic|conversions?|sales|leads)\b"
    r"|\bappear in (?:ai|chatgpt)\b"
    r"|\bguarantee(?:s|d)? (?:you|your|that|to)\b",
    re.IGNORECASE,
)

# The one sentence allowed to name the market's category terms, so buyers
# and agents searching for them can place MeshWeave. Exempt verbatim, and
# only on the surfaces listed; anywhere else the normal rules apply.
CATEGORY_SENTENCE = (
    "Some call this GEO or AEO. MeshWeave covers the site-side part, "
    "not AI visibility tracking."
)
CATEGORY_SURFACES = {"home.html", "llms.txt"}

PATTERNS = (
    (ACRONYMS, "internal acronym"),
    (RETIRED, "retired vocabulary"),
    (STANDING, "external-outcome/standing vocabulary"),
    (PROMISE, "external-outcome promise"),
)

# Copy sources as raw text: rendered-page templates (including the
# JSON-LD builders' literals in home/legal routers), agent-facing docs,
# manifest.
RAW_SOURCES = [
    *sorted((ROOT / "webapp" / "templates").rglob("*.html")),
    ROOT / "webapp" / "static" / ".well-known" / "llms.txt",
    ROOT / "webapp" / "static" / ".well-known" / "llms-full.txt",
    ROOT / "webapp" / "static" / "site.webmanifest",
    ROOT / "webapp" / "routers" / "home.py",
    ROOT / "webapp" / "routers" / "legal.py",
    ROOT / "webapp" / "routers" / "scoring.py",
    ROOT / "webapp" / "routers" / "all_public.py",
    ROOT / "webapp" / "routers" / "analysis.py",
]


def _violations(text: str) -> list[str]:
    found = []
    for pattern, kind in PATTERNS:
        for match in pattern.finditer(text):
            found.append(f"{kind}: {match.group(0)!r}")
    return found


def _scan_text(label: str, text: str) -> None:
    violations = _violations(text)
    assert not violations, f"{label} carries banned copy: {violations}"


def _iter_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _iter_strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _iter_strings(v)]
    return []


def _stub_row() -> SimpleNamespace:
    score_json = {
        "geo": {
            "composite": 65.0,
            "rating": "Connected",
            "factors": {
                "entity_consistency": {"score": 70.0, "weight": 0.35, "note": "ok"}
            },
            "skip_reasons": {},
        },
        "aeo": {
            "composite": 72.0,
            "rating": "Reliably extractable",
            "factors": {"answerability": {"score": 75.0, "weight": 0.4, "note": "ok"}},
            "skip_reasons": {},
        },
        "aax": {
            "composite": 80.0,
            "rating": "Fluent",
            "factors": {
                "homepage_comprehension": {"score": 82.0, "weight": 0.3, "note": "ok"}
            },
            "skip_reasons": {},
        },
        "recommendations": [
            {
                "factor": "entity_consistency",
                "pillar": "geo",
                "priority": "high",
                "title": "Align brand description",
                "detail": "Observed drift across pages.",
                "guidance": "State one brand description on every page.",
                "impact": "Reachable +3.4 points",
                "expected_points": 3.4,
            }
        ],
    }
    return SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        key="abc123",
        url="https://example.com",
        domain="example.com",
        path="/",
        query="",
        status="succeeded",
        visibility="private",
        scoring_version="1.3",
        canonical_url="https://example.com",
        crawl_params=None,
        updated_at=datetime(2026, 1, 15),
        created_at=datetime(2026, 1, 15),
        aeo_score=72.0,
        geo_score=65.0,
        aeo_rating="Reliably extractable",
        geo_rating="Connected",
        payload_json={
            "page": {
                "title": "Example — Free Tool",
                "description": "A free tool for example work.",
            }
        },
        score_snapshot=SimpleNamespace(
            scoring_version="1.3",
            aeo_score=72.0,
            geo_score=65.0,
            aeo_rating="Reliably extractable",
            geo_rating="Connected",
            ai_analysis_json={},
            score_json=score_json,
        ),
    )


def test_raw_copy_sources_are_clean() -> None:
    for path in RAW_SOURCES:
        text = path.read_text(encoding="utf-8")
        if path.name in CATEGORY_SURFACES:
            text = text.replace(CATEGORY_SENTENCE, "")
        _scan_text(path.relative_to(ROOT).as_posix(), text)


def test_category_sentence_only_on_allowed_surfaces() -> None:
    for path in RAW_SOURCES:
        if path.name not in CATEGORY_SURFACES:
            assert CATEGORY_SENTENCE not in path.read_text(encoding="utf-8"), path


def test_promise_pattern_catches_claims_not_nouns() -> None:
    for claim in (
        "Get cited by ChatGPT",
        "Rank higher in AI answers",
        "Get more traffic from AI",
        "We guarantee your results",
    ):
        assert _violations(claim), claim
    for honest in (
        "Recommended fixes",
        "Sources cited: 6sense, 2025",
        "We do not track competitors.",
        "Scope and price quoted by email",
    ):
        assert not _violations(honest), honest


def test_rendered_exports_are_clean() -> None:
    row = _stub_row()
    branded = render_export_markdown(
        build_export_context(
            row, site_name="MeshWeave", contact_email="ops@example.com"
        )
    )
    unbranded = render_export_markdown(
        build_export_context(row, site_name="", contact_email="")
    )
    _scan_text("branded export", branded)
    _scan_text("unbranded export", unbranded)


def test_rendered_diff_markdown_is_clean() -> None:
    row = _stub_row()
    payload = {
        "score_diff": {"composites": {"aeo": {"old": 60.0, "new": 62.0, "delta": 2.0}}},
        "findings_diff": {
            "resolved": [{"title": "Align brand description", "expected_points": 3.1}]
        },
    }
    old_row = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        domain="example.com",
        created_at=datetime(2026, 1, 1),
        score_snapshot=SimpleNamespace(score_json={}),
    )
    populated = render_diff_markdown(payload, row, old_row)
    empty = render_diff_markdown({}, row, None)
    _scan_text("diff export", populated)
    _scan_text("empty diff export", empty)


def test_interpretation_copy_is_clean() -> None:
    grid = [None, 10.0, 35.0, 55.0, 75.0, 95.0]
    for aeo in grid:
        for geo in grid:
            for aax in grid:
                interp = interpret_profile(aeo, geo, aax)
                copy_fields = {
                    key: interp.get(key)
                    for key in (
                        "profile_label",
                        "headline",
                        "diagnosis",
                        "primary_exposure",
                        "fix_priority",
                        "next_step",
                    )
                }
                for detail in (interp.get("lens_details") or {}).values():
                    copy_fields.setdefault("lens_details", [])
                    copy_fields["lens_details"].append(detail)
                for text in _iter_strings(copy_fields):
                    _scan_text("interpretation copy", text)


def test_nudge_copy_is_clean() -> None:
    for segment in ("smb", "agency"):
        for nudge in _base_nudges("result", segment, "owner", 5):
            for text in _iter_strings(nudge):
                _scan_text("nudge copy", text)


def test_export_titles_name_the_deliverable() -> None:
    row = _stub_row()
    branded = render_export_markdown(
        build_export_context(
            row, site_name="MeshWeave", contact_email="ops@example.com"
        )
    )
    unbranded = render_export_markdown(
        build_export_context(row, site_name="", contact_email="")
    )
    assert branded.startswith("# MeshWeave \u2014 Website Audit for AI Agents")
    assert unbranded.startswith("# Website Audit for AI Agents \u2014 example.com")
    diff = render_diff_markdown({}, row, None)
    assert diff.startswith("# Before/After Audit Report \u2014 example.com")


# ── API JSON responses (public preview, v1 private contract and diff) ──


def _api_builders():
    api_v1 = load_api_v1_module()
    api = sys.modules["webapp.routers.api"]
    return api_v1, api


def test_public_api_preview_is_clean() -> None:
    _, api = _api_builders()
    preview = api._build_public_preview(_stub_row())
    for text in _iter_strings(preview):
        _scan_text("public API preview", text)


def test_private_api_analysis_contract_is_clean() -> None:
    api_v1, _ = _api_builders()
    row = _stub_row()
    contract = api_v1._analysis_contract(row)
    summary = api_v1._crawl_summary(row)
    for label, blob in (("private API contract", contract), ("API summary", summary)):
        for text in _iter_strings(blob):
            _scan_text(label, text)


def test_private_api_diff_payload_is_clean() -> None:
    api_v1, _ = _api_builders()
    row = _stub_row()
    old_row = SimpleNamespace(
        id="22222222-2222-4222-8222-222222222222",
        domain="example.com",
        scoring_version="1.3",
        aeo_score=68.0,
        geo_score=62.0,
        created_at=datetime(2026, 1, 1),
        score_snapshot=SimpleNamespace(
            scoring_version="1.3",
            aeo_score=68.0,
            geo_score=62.0,
            score_json={},
        ),
    )
    payload = api_v1._diff_payload(row, old_row)
    for text in _iter_strings(payload):
        _scan_text("private API diff payload", text)


# ── Generated model copy: recommendations, ratings, implications ──


def _recommendation_scenarios() -> list[tuple[dict, dict, dict, dict]]:
    """(aeo, geo, aax, payload) inputs exercising every generator."""
    failing_questions = [
        {
            "question_id": qid,
            "question": f"Question {qid}?",
            "verdict": "unsupported",
            "missing_facts": [f"missing {qid}"],
        }
        for qid in ("offer", "audience", "use_case", "differentiation", "scope")
    ]
    aeo = {
        "answerability": {
            "score": 15.0,
            "raw": {"questions": failing_questions},
        },
        "schema": {"score": 20.0, "raw": {"coverage_pct": 20, "has_faq_schema": False}},
        "content_structure": {
            "score": 30.0,
            "raw": {
                "site_average": 30,
                "pages_evaluated": 2,
                "per_page_scores": {"https://a/": 25.0, "https://b/": 35.0},
            },
        },
    }
    geo = {
        "eeat": {"score": 20.0, "raw": {"has_org_schema": False}},
        "entity_consistency": {
            "score": 0.0,
            "raw": {"same_as": [], "name_variants": ["Acme", "Acme Inc"]},
        },
        "topical_authority": {
            "score": 20.0,
            "raw": {"coverage_pct": 10, "schema_types_count": 1},
        },
        "crawl_access": {
            "score": 25.0,
            "raw": {
                "llms_txt_exists": False,
                "robots_exists": False,
                "bot_statuses": {},
                "sitemap_count": 0,
            },
        },
        "content_depth": {
            "score": 25.0,
            "raw": {
                "avg_words": 150,
                "page_words": {"https://a/": 120},
                "pages_with_code": 0,
                "pages_with_tables": 0,
            },
        },
    }
    aax = {
        "homepage_comprehension": {
            "score": 30.0,
            "raw": {"clarity": "unclear", "information_density": "sparse"},
        },
        "content_delta": {
            "score": 25.0,
            "raw": {"weaknesses": ["No price stated"], "completeness": "adequate"},
        },
        "meta_optimization": {
            "score": 25.0,
            "raw": {"completeness": "minimal", "clarity": "unclear"},
        },
        "email_validation": {"score": 20.0, "raw": {"confidence": "low"}},
        "contactability": {"score": 15.0, "raw": {}},
    }
    confirmed_email = {
        "score": 60.0,
        "raw": {
            "confidence": "high",
            "valid_contacts": [{"email": "a@b.c", "contact_type": "general"}],
        },
    }
    payload = {
        "audit": {
            "meta": {
                "canonical_issues": ["https://a/"],
                "duplicate_og_titles": {"x": 1},
            },
            "schema_coverage": {"coverage_pct": 0, "type_counts": {}},
            "entity": {"same_as": []},
        },
        "markdowns": {
            "https://a/": {
                "content_metrics": {
                    "words": 120,
                    "images_total": 4,
                    "images_with_alt": 0,
                }
            }
        },
        "page": {},
    }
    contactability = {
        "score": 15.0,
        "has_email": False,
        "has_mailto": False,
        "has_contact_page": False,
    }
    penalised = {
        "score": 20.0,
        "has_email": True,
        "has_mailto": False,
        "has_contact_page": True,
        "penalty_points": {
            OBFUSCATED_EMAIL_PENALTY: 10.0,
            LEGAL_ONLY_EMAIL_PENALTY: 15.0,
            SAME_DOMAIN_EMAIL_PENALTY: 30.0,
        },
    }
    return [
        (
            aeo,
            geo,
            aax,
            {**payload, "scores": {"aax": {"contactability": contactability}}},
        ),
        (aeo, geo, aax, {**payload, "aax": {"contactability": contactability}}),
        (
            aeo,
            geo,
            {**aax, "email_validation": confirmed_email},
            {**payload, "scores": {"aax": {"contactability": penalised}}},
        ),
    ]


def test_recommendation_copy_is_clean() -> None:
    for aeo, geo, aax, payload in _recommendation_scenarios():
        recs = generate_recommendations(aeo, geo, payload=payload, aax_factors=aax)
        assert recs, "scenario must generate recommendations to scan"
        for rec in recs:
            for text in _iter_strings(rec):
                _scan_text("recommendation copy", text)


def test_rating_and_implication_copy_is_clean() -> None:
    for score in range(0, 101):
        for lens, rating in (
            ("aeo", aeo_rating(score)),
            ("geo", geo_rating(score)),
            ("aax", aax_rating(score)),
        ):
            _scan_text(f"{lens} rating", str(rating))
            _scan_text(f"{lens} implication", score_implication(lens, score))
