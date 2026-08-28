import contextlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from meshweave.scoring.interpretation import interpret_profile
from webapp.db import get_db
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.url import _abs_url

router = APIRouter()


def _first_sentence(text: str, limit: int = 160) -> str:
    """Return the first sentence of text, capped at limit chars."""
    try:
        t = (text or "").strip()
        if not t:
            return ""
        for sep in [". ", "। ", "。", "…", "\n"]:
            if sep in t:
                t = t.split(sep, 1)[0]
                break
        return (t[: limit - 1] + "…") if len(t) > limit else t
    except Exception:
        return ""


def _cursor_of(row: Crawl) -> str:
    """Build a stable cursor string ("epoch:id") for keyset pagination."""
    ts = int((row.updated_at or datetime.now(UTC)).timestamp())
    return f"{ts}:{row.id}"


def _counts_from_payload(row: Crawl) -> tuple[int, int]:
    """Compute (email_count, page_count) from a Crawl's payload_json."""
    try:
        p = row.payload_json or {}
        if not isinstance(p, dict):
            return 0, 0
        emails = (p.get("emails") or {}).get("unique") or []
        pages = p.get("pages") or []
        page_cnt = len(pages) if isinstance(pages, list) else 0
        return len(emails), page_cnt
    except Exception:
        return 0, 0


def _row_title(row: Crawl) -> str:
    """Title from the crawl payload, site or page scope."""
    title = ""
    try:
        if row.payload_json:
            payload = row.payload_json or {}
            if bool(row.crawl_params):
                pages = payload.get("pages") or []
                if pages and isinstance(pages, list) and len(pages) > 0:
                    title = (pages[0].get("page") or {}).get("title") or ""
            else:
                title = (payload.get("page") or {}).get("title") or ""
    except Exception:
        title = ""
    return title


def _time_fields(row: Crawl) -> tuple[str, str, bool]:
    """Return (updated_iso, updated_relative, is_new) for a row."""
    updated_dt = row.updated_at or datetime.now(UTC)
    try:
        if updated_dt.tzinfo is None:
            updated_dt = updated_dt.replace(tzinfo=UTC)
    except Exception:
        updated_dt = datetime.now(UTC)
    updated_iso = updated_dt.isoformat()
    try:
        now_ = datetime.now(UTC)
        secs = int(max(0, (now_ - updated_dt).total_seconds()))
        if secs < 60:
            updated_relative = f"{secs}s ago"
        else:
            mins = secs // 60
            if mins < 60:
                updated_relative = f"{mins}m ago"
            else:
                hrs = mins // 60
                if hrs < 24:
                    updated_relative = f"{hrs}h ago"
                else:
                    days = hrs // 24
                    updated_relative = f"{days}d ago"
    except Exception:
        updated_relative = updated_iso
    is_new = (datetime.now(UTC) - updated_dt).total_seconds() <= 2 * 3600
    return updated_iso, updated_relative, bool(is_new)


def _summary_snippet(row: Crawl) -> tuple[str, str]:
    """Return (summary_snippet, scope) for a row, site scope only."""
    try:
        scope_val = "site" if row.crawl_params else "page"
    except Exception:
        scope_val = "page"
    if scope_val != "site":
        return "", scope_val
    desc = ""
    og_desc = ""
    try:
        payload = row.payload_json or {}
        if isinstance(payload, dict):
            pg = payload.get("page") or {}
            desc = (pg.get("description") or "").strip()
            og_desc = ((pg.get("og") or {}).get("description") or "").strip()
    except Exception:
        pass
    if desc:
        return _first_sentence(desc, 160), scope_val
    if og_desc:
        return _first_sentence(og_desc, 160), scope_val
    try:
        md = (row.payload_json or {}).get("markdown") or ""
    except Exception:
        md = ""
    return _first_sentence(md, 160), scope_val


def _scores_of(row: Crawl) -> tuple[Any, Any]:
    """Extract (aax_composite, aax_rating) from the score snapshot."""
    try:
        snap = row.score_snapshot
        if snap and snap.score_json:
            aax_data = snap.score_json.get("aax") or {}
            return aax_data.get("composite"), aax_data.get("rating")
    except Exception:
        pass
    return None, None


def _headline_of(row: Crawl, aax_sc) -> tuple[Any, Any]:
    """Interpretation headline/tone for the card preview, or (None, None)."""
    try:
        if (
            row.aeo_score is not None
            and row.geo_score is not None
            and aax_sc is not None
        ):
            interp = interpret_profile(
                row.aeo_score, row.geo_score, aax_sc, score_basis="auto"
            )
            return interp.get("headline"), interp.get("tone")
    except Exception:
        pass
    return None, None


def _serialize_cell(row: Crawl, email_count, page_count) -> dict:
    """Serialize a Crawl row into a card dict for the listing template."""
    title = _row_title(row)
    updated_iso, updated_relative, is_new = _time_fields(row)
    summary_snippet, scope = _summary_snippet(row)
    aax_sc, aax_rt = _scores_of(row)
    headline, tone = _headline_of(row, aax_sc)
    return {
        "key": row.key,
        "domain": row.domain,
        "path": row.path,
        "query": row.query,
        "canonical_url": row.canonical_url,
        "title": title,
        "status": row.status,
        "scope": "site" if row.crawl_params else "page",
        "updated_at": updated_iso,
        "updated_iso": updated_iso,
        "updated_relative": updated_relative,
        "is_new": is_new,
        "email_count": int(email_count or 0),
        "page_count": int(page_count or 0),
        "aeo_score": row.aeo_score,
        "geo_score": row.geo_score,
        "aax_score": aax_sc,
        "aeo_rating": row.aeo_rating,
        "geo_rating": row.geo_rating,
        "aax_rating": aax_rt,
        "headline": headline,
        "tone": tone,
        "summary_snippet": summary_snippet if scope == "site" else "",
    }


def _base_results_query(s: Session, dom, st):
    """Base filtered query for public listed crawls."""
    q = s.query(Crawl).filter(
        Crawl.visibility == "public",
        Crawl.user_id.is_(None),
        Crawl.listed,
        Crawl.key.is_not(None),
    )
    if dom:
        q = q.filter(Crawl.domain == dom)
    if st:
        q = q.filter(Crawl.status == st)
    return q


def _parse_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    """Parse a "epoch:id" cursor into (ts, id), or (None, None)."""
    if not cursor:
        return None, None
    try:
        parts = cursor.split(":", 1)
        return datetime.fromtimestamp(int(parts[0]), tz=UTC), parts[1]
    except Exception:
        return None, None


def _normalize_page_size(page_size: int) -> int:
    """Coerce page_size to one of {12, 24, 48}."""
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 12
    return page_size if page_size in (12, 24, 48) else 12


def _normalize_domain(domain: str | None) -> str | None:
    """Strip/clean the domain filter, or None."""
    if not domain:
        return None
    dom = domain.strip().lower()
    if dom.startswith("www."):
        dom = dom[4:]
    return dom


def _normalize_status(status: str | None) -> str | None:
    """Validate the status filter against allowed values."""
    allowed_status = {"pending", "running", "succeeded", "failed"}
    return status if (status and status in allowed_status) else None


def _normalize_sort(sort: str | None) -> str:
    """Coerce sort to one of {recent, emails, pages}."""
    srt = (sort or "recent").lower()
    return srt if srt in {"recent", "emails", "pages"} else "recent"


def _normalize_direction(dir: str | None) -> str:
    """Coerce pagination direction to {next, prev}."""
    direction = (dir or "next").lower()
    return direction if direction in ("next", "prev") else "next"


def _aggregate_items(
    s: Session,
    dom: str | None,
    st: str | None,
    has_emails: bool,
    srt: str,
    page_size: int,
) -> tuple[list[dict], bool, bool]:
    """Build card items via aggregation ordering (non-recent, non-cursor)."""
    q = _base_results_query(s, dom, st)
    rows_db_raw = q.options(joinedload(Crawl.score_snapshot)).limit(500).all()

    row_counts = [(_counts_from_payload(r), r) for r in rows_db_raw]
    if has_emails:
        row_counts = [(c, r) for c, r in row_counts if c[0] > 0]
    if srt == "emails":
        row_counts.sort(
            key=lambda x: (
                -x[0][0],
                x[1].updated_at or datetime.min.replace(tzinfo=UTC),
            )
        )
    elif srt == "pages":
        row_counts.sort(
            key=lambda x: (
                -x[0][1],
                x[1].updated_at or datetime.min.replace(tzinfo=UTC),
            )
        )
    else:
        row_counts.sort(
            key=lambda x: (
                -(x[1].updated_at or datetime.min.replace(tzinfo=UTC)).timestamp()
            )
        )

    rows_db = [(r, ec, pc) for (ec, pc), r in row_counts[:page_size]]
    items = [_serialize_cell(r, ec, pc) for r, ec, pc in rows_db]
    # Aggregation branch omits keyset prev/next
    return items, False, False


def _recent_page_rows(
    s: Session, dom, st, has_emails, cursor_ts, cursor_id, direction, page_size
):
    """Query recent crawl rows with keyset pagination and count maps."""
    q = _base_results_query(s, dom, st)

    if cursor_ts and cursor_id:
        if direction == "next":
            # Older than cursor (order DESC)
            q = q.filter(
                or_(
                    Crawl.updated_at < cursor_ts,
                    and_(Crawl.updated_at == cursor_ts, Crawl.id < cursor_id),
                )
            )
            q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())
        else:
            # Newer than cursor; fetch ASC then reverse for display
            q = q.filter(
                or_(
                    Crawl.updated_at > cursor_ts,
                    and_(Crawl.updated_at == cursor_ts, Crawl.id > cursor_id),
                )
            )
            q = q.order_by(Crawl.updated_at.asc(), Crawl.id.asc())
    else:
        q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())

    rows_db_recent = (
        q.options(joinedload(Crawl.score_snapshot)).limit(page_size + 1).all()
    )

    email_counts_map: dict[int, int] = {}
    page_counts_map: dict[int, int] = {}
    for r in rows_db_recent:
        try:
            p = r.payload_json or {}
            if isinstance(p, dict):
                emails = (p.get("emails") or {}).get("unique") or []
                email_counts_map[r.id] = len(emails)
                pages_list = p.get("pages") or []
                page_counts_map[r.id] = (
                    len(pages_list) if isinstance(pages_list, list) else 0
                )
        except Exception:
            email_counts_map[r.id] = 0
            page_counts_map[r.id] = 0

    return rows_db_recent, email_counts_map, page_counts_map


def _recent_items(
    s: Session,
    dom: str | None,
    st: str | None,
    has_emails: bool,
    cursor_ts,
    cursor_id,
    direction: str,
    page_size: int,
) -> tuple[list[dict], str | None, str | None, bool, bool]:
    """Build card items and prev/next URLs for recent keyset pagination."""
    rows_db_recent, email_counts, page_counts = _recent_page_rows(
        s, dom, st, has_emails, cursor_ts, cursor_id, direction, page_size
    )

    more = len(rows_db_recent) > page_size
    rows = rows_db_recent[:page_size]
    items = [
        _serialize_cell(r, email_counts.get(r.id, 0), page_counts.get(r.id, 0))
        for r in rows
    ]

    prev_url, next_url, has_prev, has_next = _recent_nav(
        dom, st, has_emails, cursor_ts, cursor_id, direction, page_size, rows, more
    )

    return items, prev_url, next_url, has_prev, has_next


def _recent_nav(
    dom: str | None,
    st: str | None,
    has_emails: bool,
    cursor_ts,
    cursor_id,
    direction: str,
    page_size: int,
    rows: list[Crawl],
    more: bool,
) -> tuple[str | None, str | None, bool, bool]:
    """Compute keyset prev/next navigation URLs for the recent listing."""
    prev_url = None
    next_url = None
    has_prev = False
    has_next = False
    base_params = _nav_base_params(dom, st, has_emails, page_size)

    if rows:
        rows_for_nav = list(rows)
        if cursor_ts and cursor_id and direction == "prev":
            rows_for_nav = list(reversed(rows_for_nav))
        first = rows_for_nav[0]
        last = rows_for_nav[-1]

        if not (cursor_ts and cursor_id):
            if more:
                next_url, has_next = _with_cursor(base_params, "next", last)
        elif direction == "next":
            prev_url, has_prev = _with_cursor(base_params, "prev", first)
            if more:
                next_url, has_next = _with_cursor(base_params, "next", last)
        else:
            next_url, has_next = _with_cursor(base_params, "next", last)
            if more:
                prev_url, has_prev = _with_cursor(base_params, "prev", first)

    return prev_url, next_url, has_prev, has_next


def _nav_base_params(
    dom: str | None, st: str | None, has_emails: bool, page_size: int
) -> dict:
    """Base query params shared by all keyset nav URLs."""
    base_params: dict = {"page_size": str(page_size), "sort": "recent"}
    if dom:
        base_params["domain"] = dom
    if st:
        base_params["status"] = st
    if has_emails:
        base_params["has_emails"] = "1"
    return base_params


def _with_cursor(base_params: dict, nav_dir: str, row: Crawl) -> tuple[str, bool]:
    """Build a "/browse?..." nav URL with a cursor for the given row."""
    params = dict(base_params)
    params["cursor"] = _cursor_of(row)
    params["dir"] = nav_dir
    return "/browse?" + urlencode(params), True


def _page_title_text(dom: str | None, st: str | None, srt: str, site_name: str) -> str:
    """Build the SEO title for the browse page."""
    title_bits = ["Public AI search analyses"]
    if dom:
        title_bits.append(f"for {dom}")
    if st:
        title_bits.append(f"(status {st})")
    if srt and srt != "recent":
        title_bits.append(f"— sorted by {srt}")
    return " ".join(title_bits) + f" — {site_name}"


def _meta_description_text(dom: str | None, st: str | None) -> str:
    """Build the meta description for the browse page."""
    if dom and st:
        return (
            f"Explore AEO & GEO scores for {dom} with status {st}. "
            "See how this site performs for AI search."
        )
    if dom:
        return (
            f"Explore AEO & GEO scores for {dom}. See how this site "
            "performs across the factors AI engines care about."
        )
    if st:
        return (
            f"Browse public AI search analyses filtered by status {st}. "
            "See AEO & GEO scores from the community."
        )
    return (
        "Explore AEO & GEO scores submitted by the community. "
        "See how sites perform across the factors AI engines care about."
    )


def _items_json_ld(items: list[dict], dom: str | None, request: Request) -> str | None:
    """Build the ItemList JSON-LD for search engine discovery."""
    try:
        list_name = (
            f"Public AI Search Analyses for {dom}"
            if dom
            else "Public AI Search Analyses"
        )
        elements = []
        for it in items:
            try:
                elements.append(
                    {
                        "@type": "CreativeWork",
                        "name": f"Analysis for {it.get('domain') or 'site'}",
                        "identifier": it.get("key", ""),
                        "about": str(it.get("domain") or "").strip(),
                        "url": _abs_url(request, f"/analysis/{it.get('key', '')}"),
                        "dateModified": str(it.get("updated_at", ""))[:19],
                        "keywords": ["AEO", "GEO", "AI search", "optimization"],
                    }
                )
            except Exception:
                continue
        return json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "ItemList",
                "name": list_name,
                "itemListElement": elements,
            }
        )
    except Exception:
        return None


@router.get("/browse", response_class=HTMLResponse)
async def view_all(
    request: Request,
    page: int = 1,
    page_size: int = 12,
    domain: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    dir: str | None = "next",
    has_emails: bool = False,
    sort: str | None = None,
    db: Session = Depends(get_db),
):
    """Paginated listing of public results with optional filters.

    Query parameters:
      - domain: exact host (lowercase, 'www.' stripped)
      - status: one of {'pending','running','succeeded','failed'}
      - has_emails: boolean; if true only show rows with email_count > 0
      - sort: one of {'recent','emails','pages'}
      - cursor/dir: keyset pagination for 'recent' sort only
    """
    # Normalize inputs
    page_size = _normalize_page_size(page_size)
    dom = _normalize_domain(domain)
    st = _normalize_status(status)
    srt = _normalize_sort(sort)
    direction = _normalize_direction(dir)
    cursor_ts, cursor_id = _parse_cursor(cursor)

    items: list[dict] = []
    prev_url = None
    next_url = None
    has_prev = False
    has_next = False

    with contextlib.nullcontext(db) as s:
        if srt != "recent" and not cursor:
            items, has_prev, has_next = _aggregate_items(
                s, dom, st, has_emails, srt, page_size
            )
        else:
            items, prev_url, next_url, has_prev, has_next = _recent_items(
                s, dom, st, has_emails, cursor_ts, cursor_id, direction, page_size
            )

    # SEO
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    page_title = _page_title_text(dom, st, srt, site_name)
    meta_description = _meta_description_text(dom, st)

    # Canonical: keep domain/status only; exclude cursor/page_size/sort/has_emails
    canonical_params = {}
    if dom:
        canonical_params["domain"] = dom
    if st:
        canonical_params["status"] = st
    canonical_path = "/browse"
    if canonical_params:
        canonical_path = canonical_path + "?" + urlencode(canonical_params)
    abs_page_url = _abs_url(request, canonical_path)

    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: ItemList of public analyses (LLM-first)
    json_ld = _items_json_ld(items, dom, request)

    return templates.TemplateResponse(
        request,
        "all.html",
        {
            "items": items,
            "page": page,
            "page_size": page_size,
            "has_prev": has_prev,
            "has_next": has_next,
            "prev_url": prev_url,
            "next_url": next_url,
            "abs_prev_url": _abs_url(request, prev_url) if prev_url else None,
            "abs_next_url": _abs_url(request, next_url) if next_url else None,
            "filter_domain": dom or "",
            "filter_status": st or "",
            "filter_has_emails": bool(has_emails),
            "sort": srt,
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "json_ld": json_ld,
        },
    )
