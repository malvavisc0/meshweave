"""Public-copy sweep: customer-facing surfaces stay acronym-free and claim-free.

Phase-4 guard for the AI-friendly website rewrite. Scans every
customer-facing copy surface — rendered pages (template sources), exports
(rendered Markdown), JSON-LD builders, agent-facing documentation, the site
manifest, interpretation copy, and nudge copy — and asserts none of them
carries external-outcome claims or the internal AEO/GEO/AAX acronyms.

Internal score-group keys (``aeo`` / ``geo`` / ``aax``), code identifiers, and
API field names are out of scope: only rendered or published copy is checked.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# webapp.db resolves its SQLite location at import time; tests own their data.
os.environ.setdefault("SQLITE_DIR", "/tmp/kilo/meshweave-test-db")
os.environ.setdefault("SQLITE_PATH", "/tmp/kilo/meshweave-test-db/webapp.sqlite3")

from meshweave.scoring.interpretation import interpret_profile
from webapp.services.nudges import _base_nudges
from webapp.utils.export import (
    build_export_context,
    render_diff_markdown,
    render_export_markdown,
)

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
    r"|agent[- ]readability",
    re.IGNORECASE,
)

# Standing vocabulary: these words must never describe a site's standing.
STANDING = re.compile(
    r"\binvisib\w*|\bcited\b|\bcitations?\b|\bquoted\b|\brecommended\b"
    r"|\bdominant\b|\bcompetitive\b|\bcompetitors?\b",
    re.IGNORECASE,
)

PATTERNS = (
    (ACRONYMS, "internal acronym"),
    (RETIRED, "retired vocabulary"),
    (STANDING, "external-outcome/standing vocabulary"),
)

# Copy sources as raw text: rendered-page templates, agent-facing docs,
# manifest, and the routers that build JSON-LD / meta copy.
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
            "factors": {
                "extractable_answer": {"score": 75.0, "weight": 0.35, "note": "ok"}
            },
            "skip_reasons": {},
        },
        "aax": {
            "composite": 80.0,
            "rating": "Fluent",
            "factors": {
                "structure_schema": {"score": 82.0, "weight": 0.3, "note": "ok"}
            },
            "skip_reasons": {},
        },
        "recommendations": [
            {
                "pillar": "geo",
                "priority": "high",
                "title": "Align brand description",
                "detail": "Observed drift across pages.",
                "guidance": "internal",
            }
        ],
    }
    return SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        domain="example.com",
        canonical_url="https://example.com",
        crawl_params=None,
        updated_at=datetime(2026, 1, 15),
        created_at=datetime(2026, 1, 15),
        payload_json={},
        score_snapshot=SimpleNamespace(
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
        _scan_text(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))


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
    assert branded.startswith("# MeshWeave \u2014 AI-Friendly Website Report")
    assert unbranded.startswith("# AI-Friendly Website Report \u2014 example.com")
    diff = render_diff_markdown({}, row, None)
    assert diff.startswith("# AI-Friendly Progress Report \u2014 example.com")
