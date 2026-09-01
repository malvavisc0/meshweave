"""Pure diff logic for comparing two revisions of an analysis.

Everything here is side-effect free except ``find_previous_revision`` and
``list_revision_series``, which read the current revision series from the DB.
The builders take already-loaded rows/snapshots/payloads and return plain
dicts for the template.
"""

from __future__ import annotations

import difflib
from html import escape

from sqlalchemy import String, cast
from sqlalchemy.orm import joinedload

from webapp.db import get_session
from webapp.models import Crawl, ScoreSnapshot
from webapp.utils.payload_counts import json_ld_count as counts_for
from webapp.utils.scoring import FACTOR_DISPLAY_NAMES, PRIORITY_NUMERIC

# Cap each side of the unified markdown diff to bound worst-case work.
MAX_MARKDOWN_BYTES = 200_000

PILLARS = ("aeo", "geo", "aax")


def _same_series_scope(row: Crawl):
    """SQLA filter matching the base row's scope (site vs page crawl).

    Site crawls carry a ``crawl_params`` object; page crawls store none. The
    column has two "empty" representations in the wild — SQL NULL (fresh
    rows) and JSON ``'null'`` (rows rewritten by code that assigned None
    explicitly) — so both are matched for the page case. A revision series
    never mixes scopes: diffing a page run against a site run would compare
    incompatible payloads.
    """
    is_json_null = cast(Crawl.crawl_params, String) == "null"
    # Site scope is any JSON object, including {} (site crawls store their
    # limit params, possibly empty); page scope is SQL NULL or JSON 'null'.
    if isinstance(row.crawl_params, dict):
        return Crawl.crawl_params.isnot(None) & ~is_json_null
    return Crawl.crawl_params.is_(None) | is_json_null


def find_previous_revision(row: Crawl) -> Crawl | None:
    """Return the previous succeeded revision in the same series, or None.

    Matches the dedup key used by ``_find_latest_crawl``: same ``user_id``,
    ``domain``, ``path``, ``query``, and scope. Only ``succeeded`` runs count,
    and the candidate must predate ``row``. Ordered newest-first; the first
    hit is the default "compare against" revision.
    """
    if row.id is None or row.created_at is None:
        return None
    with get_session() as s:
        return (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .filter(
                Crawl.user_id == row.user_id,
                Crawl.domain == row.domain,
                Crawl.path == row.path,
                Crawl.query == row.query,
                _same_series_scope(row),
                Crawl.status == "succeeded",
                Crawl.created_at < row.created_at,
            )
            .order_by(Crawl.created_at.desc())
            .first()
        )


def list_revision_series(row: Crawl) -> list[dict]:
    """Return every succeeded revision in the same series, oldest first.

    Includes the base row (which is succeeded when this is called) and spans
    visibility: an owner's public and private runs of the same page appear in
    one series. Never crosses to another user's rows or to the opposite scope
    (page vs site). Each entry is serialized as
    ``{id, created_at, status, visibility, scope}``.
    """
    if row.id is None:
        return []
    with get_session() as s:
        rows = (
            s.query(Crawl)
            .filter(
                Crawl.user_id == row.user_id,
                Crawl.domain == row.domain,
                Crawl.path == row.path,
                Crawl.query == row.query,
                _same_series_scope(row),
                Crawl.status == "succeeded",
            )
            .order_by(Crawl.created_at.asc())
            .all()
        )
    return [
        {
            "id": r.id,
            "created_at": r.created_at,
            "status": r.status,
            "visibility": r.visibility,
            "scope": "site" if r.crawl_params else "page",
        }
        for r in rows
    ]


def _round_delta(old: float | None, new: float | None) -> float | None:
    """Round the difference between two scores, or None when either is missing."""
    if old is not None and new is not None:
        return round(float(new) - float(old), 1)
    return None


def _composite_from_snapshot(ss: ScoreSnapshot | None, pillar: str) -> float | None:
    """Return ``score_json[pillar].composite`` for a snapshot, or None."""
    if not ss:
        return None
    section = (ss.score_json or {}).get(pillar) or {}
    composite = section.get("composite")
    if isinstance(composite, bool) or not isinstance(composite, (int, float)):
        return None
    return float(composite)


def _row_composite(row: Crawl | None, pillar: str) -> float | None:
    """Return the row-level composite for aeo/geo (aax is snapshot-only)."""
    if row is None or pillar not in ("aeo", "geo"):
        return None
    return getattr(row, f"{pillar}_score", None)


def build_score_diff(
    old_ss: ScoreSnapshot | None,
    new_ss: ScoreSnapshot | None,
    old_row: Crawl | None = None,
    new_row: Crawl | None = None,
) -> dict:
    """Compare two score snapshots into composite deltas and per-factor rows.

    Composites fall back to the row-level ``aeo_score``/``geo_score`` when a
    snapshot is missing. The factor list is empty when either snapshot is
    missing, and the template shows a "scores unavailable" note in that case.
    """
    composites = {}
    for pillar in PILLARS:
        old_composite = _composite_from_snapshot(old_ss, pillar)
        new_composite = _composite_from_snapshot(new_ss, pillar)
        if old_composite is None:
            old_composite = _row_composite(old_row, pillar)
        if new_composite is None:
            new_composite = _row_composite(new_row, pillar)
        composites[pillar] = {
            "old": old_composite,
            "new": new_composite,
            "delta": _round_delta(old_composite, new_composite),
        }
    return {
        "composites": composites,
        "factors": _build_factor_rows(old_ss, new_ss),
    }


def _factors_by_pillar(ss: ScoreSnapshot | None) -> dict[str, dict]:
    """Return ``{pillar: {factor_key: factor_dict}}`` for a snapshot (empty if none)."""
    out: dict[str, dict] = {}
    if not ss:
        return out
    score_json = ss.score_json or {}
    for pillar in PILLARS:
        section = score_json.get(pillar) or {}
        out[pillar] = section.get("factors") or {}
    return out


def _factor_display_name(key: str) -> str:
    """Human-readable factor name via the display map, with a title() fallback."""
    return FACTOR_DISPLAY_NAMES.get(key, key.replace("_", " ").title())


def _factor_status_change(old_score: float | None, new_score: float | None) -> str:
    """Classify how a single factor moved between two runs."""
    if old_score is not None and new_score is not None:
        if new_score > old_score:
            return "improved"
        if new_score < old_score:
            return "regressed"
        return "unchanged"
    if old_score is not None and new_score is None:
        return "disappeared"
    if old_score is None and new_score is not None:
        return "appeared"
    return "unchanged"


def _build_factor_rows(
    old_ss: ScoreSnapshot | None, new_ss: ScoreSnapshot | None
) -> list[dict]:
    """Union of factor keys across both snapshots, one diff row per factor."""
    old_factors = _factors_by_pillar(old_ss)
    new_factors = _factors_by_pillar(new_ss)
    rows: list[dict] = []
    for pillar in PILLARS:
        keys = set(old_factors.get(pillar, {})) | set(new_factors.get(pillar, {}))
        for key in sorted(keys):
            old_factor = old_factors.get(pillar, {}).get(key) or {}
            new_factor = new_factors.get(pillar, {}).get(key) or {}
            old_score = old_factor.get("score")
            new_score = new_factor.get("score")
            rows.append(
                {
                    "pillar": pillar,
                    "key": key,
                    "display_name": _factor_display_name(key),
                    "old": old_score,
                    "new": new_score,
                    "delta": _round_delta(old_score, new_score),
                    "status_change": _factor_status_change(old_score, new_score),
                }
            )
    return rows


def _recommendations(ss: ScoreSnapshot | None) -> list[dict]:
    """Return the raw recommendation list for a snapshot (empty when none)."""
    if not ss:
        return []
    return (ss.score_json or {}).get("recommendations") or []


def _recommendation_id(rec: dict) -> tuple[str, str]:
    """Stable identity for a recommendation: factor key, else exact title."""
    factor = rec.get("factor")
    if factor:
        return ("factor", factor)
    return ("title", rec.get("title") or "")


def _sort_recommendations(recs: list[dict]) -> list[dict]:
    """Sort recommendations high-priority first (stable within a band)."""
    return sorted(
        recs,
        key=lambda r: PRIORITY_NUMERIC.get(r.get("priority", "medium"), 1),
    )


def build_findings_diff(
    old_ss: ScoreSnapshot | None, new_ss: ScoreSnapshot | None
) -> dict:
    """Compare recommendations between two runs.

    A recommendation is identified by its ``factor`` key when present (so
    "Enrich 3 thin page(s)" and "Enrich 5 thin page(s)" match) and by exact
    title otherwise. Returns resolved (old-only), newly triggered (new-only),
    and the unchanged count. Each resolved recommendation that carried
    ``expected_points`` gains ``observed_delta`` — the actual lens composite
    movement between the two snapshots — so the page can show the model's
    prediction against what happened.
    """
    old_recs = {_recommendation_id(r): r for r in _recommendations(old_ss)}
    new_recs = {_recommendation_id(r): r for r in _recommendations(new_ss)}
    resolved = [old_recs[k] for k in old_recs if k not in new_recs]
    newly = [new_recs[k] for k in new_recs if k not in old_recs]
    unchanged_count = sum(1 for k in old_recs if k in new_recs)

    composite_deltas = _lens_composite_deltas(old_ss, new_ss)
    for rec in resolved:
        _attach_observed_delta(rec, composite_deltas)

    return {
        "resolved": _sort_recommendations(resolved),
        "new": _sort_recommendations(newly),
        "unchanged_count": unchanged_count,
    }


_LENS_COMPOSITE_KEYS: dict[str, str] = {
    "aeo": "aeo_score",
    "geo": "geo_score",
}


def _lens_composite_deltas(
    old_ss: ScoreSnapshot | None, new_ss: ScoreSnapshot | None
) -> dict[str, float | None]:
    """Signed composite deltas per lens between two snapshots."""
    deltas = {
        lens: _numeric_delta(
            getattr(old_ss, attr, None) if old_ss else None,
            getattr(new_ss, attr, None) if new_ss else None,
        )
        for lens, attr in _LENS_COMPOSITE_KEYS.items()
    }
    deltas["aax"] = _numeric_delta(
        _aax_composite(old_ss),
        _aax_composite(new_ss),
    )
    return deltas


def _aax_composite(ss: ScoreSnapshot | None) -> float | None:
    """The AAX composite stored inside a snapshot's score_json."""
    if not ss:
        return None
    return ((ss.score_json or {}).get("aax") or {}).get("composite")


def _numeric_delta(old: float | None, new: float | None) -> float | None:
    """new − old rounded to one decimal; None when either side is absent."""
    if old is None or new is None:
        return None
    return round(float(new) - float(old), 1)


def _attach_observed_delta(
    rec: dict, composite_deltas: dict[str, float | None]
) -> None:
    """Record the observed lens movement on a resolved recommendation.

    Only attaches when the old recommendation carried a predicted
    ``expected_points`` — a prediction is only checkable when it was
    made.
    """
    if rec.get("expected_points") is None:
        return
    rec["observed_delta"] = composite_deltas.get(rec.get("pillar") or "")


def _page_meta(payload: dict | None) -> dict:
    """Return the page metadata dict for a payload (page scope or first site page)."""
    if not payload or not isinstance(payload, dict):
        return {}
    page = payload.get("page")
    if isinstance(page, dict):
        return page
    pages = payload.get("pages")
    if isinstance(pages, list) and pages:
        first = pages[0]
        if isinstance(first, dict):
            nested = first.get("page")
            if isinstance(nested, dict):
                return nested
            return first
    return {}


def _title_desc(payload: dict | None) -> dict:
    """Return {title, description} extracted from a payload's page metadata."""
    meta = _page_meta(payload)
    return {
        "title": (meta.get("title") or "").strip(),
        "description": (meta.get("description") or "").strip(),
    }


def _is_site_scope(payload: dict | None) -> bool:
    """True when the payload is a site-scope crawl (has a pages list)."""
    return payload is not None and isinstance(payload.get("pages"), list)


def _pages_by_url(payload: dict | None) -> dict[str, dict]:
    """Map each site page to its ``url`` key for membership diffs."""
    out: dict[str, dict] = {}
    if payload is None or not isinstance(payload.get("pages"), list):
        return out
    for page in payload["pages"]:
        if not isinstance(page, dict):
            continue
        url = page.get("url")
        if not url:
            nested = page.get("page")
            if isinstance(nested, dict):
                url = nested.get("url")
        if url:
            out[str(url)] = page
    return out


def _page_list_diff(old_payload: dict | None, new_payload: dict | None) -> dict:
    """Return added/removed page URLs (by url key) across two site payloads."""
    old_urls = set(_pages_by_url(old_payload))
    new_urls = set(_pages_by_url(new_payload))
    return {
        "added": sorted(new_urls - old_urls),
        "removed": sorted(old_urls - new_urls),
    }


def _cap_markdown(text: str) -> tuple[str, bool]:
    """Truncate markdown to the diff size cap; return (text, truncated)."""
    if len(text.encode("utf-8", "replace")) <= MAX_MARKDOWN_BYTES:
        return text, False
    budget = text.encode("utf-8", "replace")[:MAX_MARKDOWN_BYTES]
    truncated_text = budget.decode("utf-8", "ignore")
    return truncated_text, True


def _render_unified_diff(
    old_payload: dict | None, new_payload: dict | None
) -> tuple[str, bool]:
    """Return (html, truncated) unified diff of two page-scope payloads."""
    old_text, old_truncated = _cap_markdown((old_payload or {}).get("markdown", ""))
    new_text, new_truncated = _cap_markdown((new_payload or {}).get("markdown", ""))
    diff = list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="previous",
            tofile="current",
            n=3,
        )
    )
    if not diff:
        return "", old_truncated or new_truncated
    html_lines = []
    for line in diff:
        stripped = line.rstrip("\n")
        if line.startswith("+"):
            css_class = "diff-added"
        elif line.startswith("-"):
            css_class = "diff-removed"
        else:
            css_class = "diff-context"
        html_lines.append(f'<span class="{css_class}">{escape(stripped)}</span>')
    return "\n".join(html_lines), old_truncated or new_truncated


def build_content_diff(old_payload: dict | None, new_payload: dict | None) -> dict:
    """Compare payloads: title/description, count deltas, and scope-appropriate detail.

    Page scope renders a collapsed unified markdown diff; site scope renders a
    page-list membership diff (added/removed URLs). Either side missing yields
    ``available: False`` and empty detail sections.
    """
    available = old_payload is not None and new_payload is not None
    scope = "site" if _is_site_scope(new_payload) else "page"
    counts = {
        key: {
            "old": counts_for(old_payload).get(key, 0),
            "new": counts_for(new_payload).get(key, 0),
            "delta": counts_for(new_payload).get(key, 0)
            - counts_for(old_payload).get(key, 0),
        }
        for key in (
            "content_pages_count",
            "emails_count",
            "internal_links_count",
            "external_links_count",
        )
    }
    markdown_diff_html = ""
    markdown_truncated = False
    page_list: dict | None = None
    if available:
        if scope == "page":
            markdown_diff_html, markdown_truncated = _render_unified_diff(
                old_payload, new_payload
            )
        else:
            page_list = _page_list_diff(old_payload, new_payload)
    return {
        "scope": scope,
        "available": available,
        "title_desc": {
            "old": _title_desc(old_payload),
            "new": _title_desc(new_payload),
        },
        "counts": counts,
        "markdown_diff_html": markdown_diff_html,
        "markdown_truncated": markdown_truncated,
        "page_list": page_list,
    }


def _scoring_meta(
    ss: ScoreSnapshot | None, row: Crawl | None
) -> tuple[str | None, bool]:
    """Return (scoring_version, has_manual_input) from snapshot, else row."""
    if ss is not None:
        return (
            getattr(ss, "scoring_version", None),
            bool(getattr(ss, "has_manual_input", False)),
        )
    return (
        getattr(row, "scoring_version", None),
        bool(getattr(row, "has_manual_input", False)),
    )


def build_comparison_notes(
    old_row: Crawl | None,
    new_row: Crawl | None,
    old_ss: ScoreSnapshot | None,
    new_ss: ScoreSnapshot | None,
) -> list[str]:
    """Return honest-comparison banners when the two runs are not apples-to-apples.

    Flags a scoring_version mismatch (factor comparison may reflect algorithm
    changes, not page changes) and a manual-input mismatch (composites are on
    different bases).
    """
    notes: list[str] = []
    old_version, old_manual = _scoring_meta(old_ss, old_row)
    new_version, new_manual = _scoring_meta(new_ss, new_row)
    if (
        old_version is not None
        and new_version is not None
        and old_version != new_version
    ):
        notes.append(
            "Scores were computed with different scoring versions; factor-level "
            "comparison may not reflect page changes alone."
        )
    if bool(old_manual) != bool(new_manual):
        notes.append(
            "One run includes manual inputs; composites are not on the same basis."
        )
    return notes
