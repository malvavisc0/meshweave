"""Tests for the owner-only report export (webapp.utils.export).

Pure-function coverage only: the serializer, the filename helper, the
context builder (against a SimpleNamespace stub — no database, no ORM), and
the Markdown renderer. Endpoint/HTTP behavior is out of scope for the root
test environment (no fastapi).
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from webapp.utils.export import (
    build_export_context,
    export_recommendation,
    render_export_markdown,
    safe_filename,
)


# --------------------------------------------------------------------------
# Serializer (§9.1)
# --------------------------------------------------------------------------
def test_serializer_keeps_only_allowlisted_fields() -> None:
    rec = export_recommendation(
        {
            "pillar": "aax",
            "priority": "high",
            "title": "T",
            "detail": "D",
            "guidance": "internal",
            "impact": "x",
        }
    )
    assert set(rec) == {"pillar", "priority", "title", "detail"}
    assert "guidance" not in rec and "impact" not in rec


def test_serializer_bounds_fields() -> None:
    rec = export_recommendation(
        {
            "title": "a" * 300,
            "detail": "b" * 900,
            "pillar": "P" * 20,
            "priority": "Q" * 20,
        }
    )
    assert len(rec["title"]) == 200
    assert len(rec["detail"]) == 500
    assert len(rec["pillar"]) == 12
    assert len(rec["priority"]) == 12


def test_serializer_defaults_for_missing_keys() -> None:
    rec = export_recommendation({})
    assert rec["priority"] == "info"
    assert rec["title"] == ""
    assert rec["detail"] == ""
    assert rec["pillar"] == ""


def test_serializer_normalizes_pillar_and_priority_case() -> None:
    rec = export_recommendation({"pillar": "aax", "priority": "High"})
    assert rec["pillar"] == "AAX"
    assert rec["priority"] == "high"


def test_serializer_preserves_input_order() -> None:
    recs = [{"title": "1"}, {"title": "2"}, {"title": "3"}]
    out = [export_recommendation(r) for r in recs]
    assert [r["title"] for r in out] == ["1", "2", "3"]


# --------------------------------------------------------------------------
# Filename (§9.2)
# --------------------------------------------------------------------------
def test_safe_filename_normal_domain() -> None:
    assert safe_filename("example.com") == "example.com"


def test_safe_filename_case_spaces_and_unicode() -> None:
    assert safe_filename("Example.COM") == "example.com"
    assert safe_filename("Example  Com/Ü") == "example-com"


def test_safe_filename_rejects_crlf_and_blank() -> None:
    assert "\n" not in safe_filename("a\rb\nc")
    assert safe_filename("a\rb\nc") == "a-b-c"
    assert safe_filename(None) == "site"
    assert safe_filename("") == "site"
    assert safe_filename("   ") == "site"


def test_safe_filename_is_bounded() -> None:
    assert len(safe_filename("a" * 200 + ".com")) <= 80


# --------------------------------------------------------------------------
# Stub row / snapshot
# --------------------------------------------------------------------------
def _stub_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        domain="example.com",
        canonical_url="https://example.com",
        crawl_params=None,
        updated_at=datetime(2026, 1, 15, 12, 30),
        score_snapshot=SimpleNamespace(
            aeo_score=72.0,
            geo_score=65.0,
            aeo_rating="Strong",
            geo_rating="Visible",
            has_manual_input=False,
            ai_analysis_json={"aax": {"summary": "ok"}},
            score_json={
                "aeo": {
                    "composite": 72.0,
                    "rating": "Strong",
                    "factors": {},
                    "skip_reasons": {},
                },
                "geo": {
                    "composite": 65.0,
                    "rating": "Visible",
                    "factors": {},
                    "skip_reasons": {},
                },
                "aax": {
                    "composite": 80.0,
                    "rating": "Fluent",
                    "factors": {},
                    "skip_reasons": {},
                    "tests_completed": 5,
                    "tests_skipped": 0,
                },
                "recommendations": [
                    {
                        "pillar": "aax",
                        "priority": "high",
                        "title": "Fix offer",
                        "detail": "Clarify the offer",
                        "guidance": "PRIVATE PAGE BODY guidance",
                    },
                    {
                        "pillar": "aeo",
                        "priority": "medium",
                        "title": "Add FAQ",
                        "detail": "Add FAQ schema",
                        "impact": "secret-impact",
                    },
                ],
            },
        ),
        # Sentinels that must never cross the export boundary.
        payload={
            "emails": {"unique": ["private-contact@example.com"]},
            "page": {"body": "PRIVATE PAGE BODY"},
        },
    )


def _build() -> dict:
    return build_export_context(
        _stub_row(), site_name="MeshWeave", contact_email="ops@meshweaveai.com"
    )


# --------------------------------------------------------------------------
# Context builder (§9.3)
# --------------------------------------------------------------------------
def test_context_has_exactly_documented_keys() -> None:
    ctx = _build()
    assert set(ctx) == {
        "site_name",
        "domain",
        "canonical_url",
        "scope",
        "report_date",
        "scores",
        "interpretation",
        "recommendations",
        "consultation_email",
    }


def test_context_excludes_private_data() -> None:
    ctx = _build()
    blob = json.dumps(ctx)
    assert "private-contact@example.com" not in blob
    assert "PRIVATE PAGE BODY" not in blob
    assert "secret-impact" not in blob
    # Crawl UUID must never cross the boundary.
    assert "11111111-1111-4111-8111-111111111111" not in blob


def test_context_report_date_is_utc_day() -> None:
    assert _build()["report_date"] == "2026-01-15"


def test_context_report_date_treats_naive_as_utc() -> None:
    row = _stub_row()
    row.updated_at = datetime(2026, 3, 4, 5, 6)  # naive
    ctx = build_export_context(row, site_name="s", contact_email="e")
    assert ctx["report_date"] == "2026-03-04"


def test_context_scope_follows_crawl_params() -> None:
    assert _build()["scope"] == "page"
    row = _stub_row()
    row.crawl_params = {"crawl": True}
    assert (
        build_export_context(row, site_name="s", contact_email="e")["scope"] == "site"
    )


def test_context_scores_and_sorted_recommendations() -> None:
    ctx = _build()
    assert set(ctx["scores"]) == {"aeo", "geo", "aax"}
    assert ctx["scores"]["aax"]["rating"] == "Fluent"
    assert ctx["scores"]["aax"]["score"] == 80.0
    # High-priority AAX recommendation sorts first.
    assert [r["pillar"] for r in ctx["recommendations"]] == ["AAX", "AEO"]


# --------------------------------------------------------------------------
# Markdown renderer (replaces plan §9.4 template-structural tests)
# --------------------------------------------------------------------------
def test_markdown_contains_all_sections_and_identifiers() -> None:
    md = render_export_markdown(_build())
    assert md.startswith("# MeshWeave — AI Visibility Report")
    assert "## Executive Summary" in md
    assert "## Scores" in md
    assert "## Recommendations" in md
    assert "## Methodology" in md
    assert "## Next Step" in md
    assert "example.com" in md
    assert "ops@meshweaveai.com" in md
    assert "AAX" in md and "AEO" in md and "GEO" in md


def test_markdown_lists_recommendations_in_order() -> None:
    md = render_export_markdown(_build())
    assert md.index("### [High] Fix offer") < md.index("### [Medium] Add FAQ")


def test_markdown_excludes_guidance_and_sentinels() -> None:
    md = render_export_markdown(_build())
    assert "PRIVATE PAGE BODY" not in md
    assert "private-contact@example.com" not in md
    assert "secret-impact" not in md
    assert "guidance" not in md.lower()


def test_markdown_contains_fixed_methodology_copy() -> None:
    md = render_export_markdown(_build())
    assert "diagnostic signals" in md
    assert "browser-agent" in md


def test_markdown_table_cells_escape_pipes_and_newlines() -> None:
    ctx = _build()
    ctx["scores"]["aax"]["rating"] = "Strong | Fluent\nmulti-line"
    md = render_export_markdown(ctx)
    assert "Strong \\| Fluent multi-line" in md
    # The raw pipe must not survive unescaped inside a cell.
    assert "Strong | Fluent" not in md
