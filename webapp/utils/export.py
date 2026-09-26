"""Owner-only report export.

Pure, dependency-free helpers (stdlib plus ``webapp.utils.scoring``) that turn a
detached ``Crawl`` row into a shareable consultation artifact. Kept free of
fastapi / sqlalchemy / prometheus_client imports so the logic is unit-testable
without the webapp runtime stack installed.
"""

from __future__ import annotations

import re
from datetime import datetime

from webapp.utils.scoring import (
    _sorted_recommendations,
    build_score_snapshot_context,
)
from webapp.utils.times import ensure_utc


def export_recommendation(rec: dict) -> dict:
    """Serialize a recommendation to the export allowlist (four bounded fields).

    Keeps only ``pillar``, ``priority``, ``title``, and ``detail``. ``guidance``
    and every other field are dropped — they carry internal remediation
    instructions. Length bounds are the defense against a saved artifact
    rendering an unbounded blob.
    """
    return {
        "pillar": str(rec.get("pillar") or "").upper()[:12],
        "priority": str(rec.get("priority") or "info").lower()[:12],
        "title": str(rec.get("title") or "")[:200],
        "detail": str(rec.get("detail") or "")[:500],
    }


def export_answerability_question(q: dict) -> dict:
    """Serialize one answerability record to the export allowlist.

    Keeps the question, its verdict, the grounded answer, the missing
    facts, and the source pages — the evidence a reader needs to see why
    an answer fails. Length bounds are the defense against a saved
    artifact rendering an unbounded blob.
    """
    return {
        "question_id": str(q.get("question_id") or "")[:40],
        "question": str(q.get("question") or "")[:300],
        "verdict": str(q.get("verdict") or "")[:32],
        "answer": str(q.get("answer") or "")[:2000],
        "missing_facts": [str(f)[:300] for f in (q.get("missing_facts") or [])[:10]][
            :10
        ],
        "source_pages": [str(u)[:300] for u in (q.get("source_pages") or [])[:20]][:20],
    }


def answerability_questions(score_json: dict | None) -> list[dict]:
    """Per-question answerability evidence from the stored factor raw.

    Reads only ``score_json`` (a snapshot column) — never the payload or
    the AI analysis blob.
    """
    factors = ((score_json or {}).get("aeo") or {}).get("factors") or {}
    raw = (factors.get("answerability") or {}).get("raw") or {}
    return [
        export_answerability_question(q)
        for q in (raw.get("questions") or [])
        if isinstance(q, dict)
    ]


def answerability_movement(
    old_score_json: dict | None, new_score_json: dict | None
) -> list[dict]:
    """Per-question verdict movement between two runs (re-check evidence)."""
    old = {q["question_id"]: q for q in answerability_questions(old_score_json)}
    rows: list[dict] = []
    for q in answerability_questions(new_score_json):
        before = (old.get(q["question_id"]) or {}).get("verdict") or ""
        rows.append(
            {
                "question_id": q["question_id"],
                "question": q["question"],
                "verdict_before": before,
                "verdict_after": q["verdict"],
            }
        )
    return rows


def safe_filename(domain: str | None) -> str:
    """Sanitize a domain for use in a ``Content-Disposition`` filename.

    The allowlist charset excludes CR/LF, so header injection through
    ``Content-Disposition`` is impossible by construction.
    """
    value = (domain or "site").lower()
    value = re.sub(r"[^a-z0-9.-]+", "-", value).strip("-.")
    return value[:80] or "site"


def _report_date(row) -> str:
    """Format ``row.updated_at`` as ``YYYY-MM-DD`` in UTC (naive assumed UTC)."""
    updated = getattr(row, "updated_at", None)
    if not isinstance(updated, datetime):
        return ""
    return ensure_utc(updated).strftime("%Y-%m-%d")


def build_export_context(row, *, site_name: str, contact_email: str) -> dict:
    """Build the export context from a detached ``Crawl`` row.

    Reads only column attributes and the eager-loaded ``score_snapshot``; never
    ``payload``, the crawl UUID, ``ai_analysis_json``, ``score_data``, or
    ``aax_analysis``. ``interpretation`` passes through whole; recommendations
    are serialized through :func:`export_recommendation` in sorted order and
    answerability evidence through :func:`export_answerability_question` from
    the snapshot's ``score_json`` factor raw.
    """
    score_ctx = build_score_snapshot_context(row) or {}
    sorted_recs = _sorted_recommendations(score_ctx)
    snap = getattr(row, "score_snapshot", None)

    return {
        "site_name": str(site_name),
        "domain": row.domain,
        "canonical_url": row.canonical_url,
        "scope": "site" if row.crawl_params else "page",
        "report_date": _report_date(row),
        "scores": {
            lens: {
                "score": score_ctx.get(f"{lens}_score"),
                "rating": score_ctx.get(f"{lens}_rating"),
                "implication": score_ctx.get(f"{lens}_implication"),
            }
            for lens in ("aeo", "geo", "aax")
        },
        "interpretation": score_ctx.get("interpretation") or {},
        "answerability": answerability_questions(getattr(snap, "score_json", None)),
        "recommendations": [export_recommendation(r) for r in sorted_recs],
        "consultation_email": str(contact_email),
    }


def _fmt_score(score) -> str:
    """Render a score for Markdown: em dash when unavailable, else one decimal."""
    if score is None:
        return "—"
    return str(round(float(score), 1))


def _md_cell(value) -> str:
    """Render a table cell: escape pipes and collapse newlines."""
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _md_header(ctx: dict) -> str:
    site_name = (ctx.get("site_name") or "").strip()
    title = (
        f"# {site_name} — AI Visibility Report"
        if site_name
        else f"# AI Visibility Report — {ctx['domain']}"
    )
    lines = [
        title,
        "",
        f"**Domain:** {ctx['domain']}",
        f"**Scope:** {ctx['scope']}",
    ]
    if ctx["report_date"]:
        lines.append(f"**Report date:** {ctx['report_date']}")
    lines.append("")
    return "\n".join(lines)


def _md_executive_summary(ctx: dict) -> str:
    interp = ctx.get("interpretation") or {}
    lines: list[str] = ["## Executive Summary", ""]
    if interp.get("profile_label"):
        lines.append(f"### {interp['profile_label']}")
        lines.append("")
    if interp.get("headline"):
        lines.append(interp["headline"])
        lines.append("")
    if interp.get("diagnosis"):
        lines.append(interp["diagnosis"])
        lines.append("")
    return "\n".join(lines)


def _md_scores(ctx: dict) -> str:
    scores = ctx["scores"]
    lines = [
        "## Scores",
        "",
        "| Lens | Score | Rating | Implication |",
        "| --- | --- | --- | --- |",
    ]
    for lens in ("aax", "aeo", "geo"):
        s = scores.get(lens) or {}
        lines.append(
            f"| {lens.upper()} | {_fmt_score(s.get('score'))} "
            f"| {_md_cell(s.get('rating'))} | {_md_cell(s.get('implication'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _md_answerability(ctx: dict) -> str:
    questions = ctx.get("answerability") or []
    lines: list[str] = ["## Answerability Evidence", ""]
    if not questions:
        lines.append("_The grounded answer test did not run for this crawl._")
        lines.append("")
        return "\n".join(lines)
    for q in questions:
        lines.append(f"### {q.get('question') or 'Question'}")
        lines.append("")
        lines.append(f"- **Verdict:** {q.get('verdict') or '—'}")
        if q.get("answer"):
            lines.append(f"- **Answer:** {_md_cell(q['answer'])}")
        if q.get("missing_facts"):
            lines.append(f"- **Missing:** {_md_cell('; '.join(q['missing_facts']))}")
        if q.get("source_pages"):
            lines.append(f"- **Sources:** {_md_cell(', '.join(q['source_pages']))}")
        lines.append("")
    return "\n".join(lines)


def _md_recommendations(ctx: dict) -> str:
    recs = ctx.get("recommendations") or []
    lines = ["## Recommendations", ""]
    if not recs:
        lines.append("_No recommendations._")
        lines.append("")
        return "\n".join(lines)
    for rec in recs:
        priority = str(rec.get("priority") or "").title()
        lines.append(f"### [{priority}] {rec.get('title') or 'Untitled'}")
        lines.append("")
        lines.append(f"- **Lens:** {rec.get('pillar') or 'Unknown'}")
        if rec.get("detail"):
            lines.append(f"- **Detail:** {rec['detail']}")
        lines.append("")
    return "\n".join(lines)


def _md_methodology(ctx: dict) -> str:
    lines = ["## Methodology & Limitations", ""]
    lines.append(
        "Scores are diagnostic signals, not guarantees of rankings, citations, "
        "traffic, revenue, or conversion. AAX is not an interactive "
        "browser-agent or transaction test."
    )
    lines.append("")
    return "\n".join(lines)


def _md_next_step(ctx: dict) -> str:
    interp = ctx.get("interpretation") or {}
    email = (ctx.get("consultation_email") or "").strip()
    if not interp.get("next_step") and not email:
        # Unbranded export with nothing to say: omit the section entirely
        # rather than dangling a "contact ." line.
        return ""
    lines: list[str] = ["## Next Step", ""]
    if interp.get("next_step"):
        lines.append(interp["next_step"])
        lines.append("")
    if email:
        lines.append(f"For expert review or remediation, contact {email}.")
        lines.append("")
    return "\n".join(lines)


def render_export_markdown(ctx: dict) -> str:
    """Render the export context as a plain-Markdown artifact for agents."""
    blocks = [
        _md_header(ctx),
        _md_executive_summary(ctx),
        _md_scores(ctx),
        _md_answerability(ctx),
        _md_recommendations(ctx),
        _md_methodology(ctx),
        _md_next_step(ctx),
    ]
    return "\n\n".join(block for block in blocks if block).rstrip() + "\n"
