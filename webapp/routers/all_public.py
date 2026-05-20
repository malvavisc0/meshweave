import contextlib
import json
import os
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from webapp.db import get_db
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/all", response_class=HTMLResponse)
async def view_all(
    request: Request,
    page: int = 1,
    page_size: int = 50,
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
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 50
    if page_size not in (25, 50, 100):
        page_size = 50

    dom = None
    if domain:
        dom = domain.strip().lower()
        if dom.startswith("www."):
            dom = dom[4:]

    allowed_status = {"pending", "running", "succeeded", "failed"}
    st = status if (status and status in allowed_status) else None

    allowed_sorts = {"recent", "emails", "pages"}
    srt = (sort or "recent").lower()
    if srt not in allowed_sorts:
        srt = "recent"

    direction = (dir or "next").lower()
    if direction not in ("next", "prev"):
        direction = "next"

    # Parse cursor of form "epoch:id" (used for 'recent' only)
    cursor_ts = None
    cursor_id = None
    if cursor:
        try:
            parts = cursor.split(":", 1)
            cursor_ts = datetime.fromtimestamp(int(parts[0]), tz=UTC)
            cursor_id = parts[1]
        except Exception:
            cursor_ts = None
            cursor_id = None

    items = []
    prev_url = None
    next_url = None

    with contextlib.nullcontext(db) as s:
        if srt != "recent" and not cursor:
            # Aggregation ordering — query Crawl only, compute counts from payload_json
            q = s.query(Crawl).filter(
                Crawl.visibility == "public",
                Crawl.user_id.is_(None),
                Crawl.listed,
            )
            if dom:
                q = q.filter(Crawl.domain == dom)
            if st:
                q = q.filter(Crawl.status == st)

            rows_db_raw = q.limit(500).all()

            # Compute email/page counts from payload_json
            def _counts_from_payload(row: Crawl) -> tuple[int, int]:
                try:
                    p = json.loads(row.payload_json) if row.payload_json else {}
                    if not isinstance(p, dict):
                        return 0, 0
                    emails = (p.get("emails") or {}).get("unique") or []
                    pages = p.get("pages") or []
                    page_cnt = len(pages) if isinstance(pages, list) else 0
                    return len(emails), page_cnt
                except Exception:
                    return 0, 0

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
                        -(
                            x[1].updated_at or datetime.min.replace(tzinfo=UTC)
                        ).timestamp()
                    )
                )

            rows_db = [(r, ec, pc) for (ec, pc), r in row_counts[:page_size]]

            for row, email_count, page_count in rows_db:
                title = ""
                payload = None
                try:
                    if row.payload_json:
                        payload = json.loads(row.payload_json)
                        if bool(row.crawl_params):
                            pages = payload.get("pages") or []
                            if pages and isinstance(pages, list) and len(pages) > 0:
                                title = (pages[0].get("page") or {}).get("title") or ""
                        else:
                            title = (payload.get("page") or {}).get("title") or ""
                except Exception:
                    title = ""

                # Compute relative/iso times and "new" flag (2h threshold)
                updated_dt = row.updated_at or datetime.now(UTC)
                try:
                    if updated_dt.tzinfo is None:
                        updated_dt = updated_dt.replace(tzinfo=UTC)
                except Exception:
                    updated_dt = datetime.now(UTC)
                updated_iso = updated_dt.isoformat()
                # Relative time
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

                # Summary snippet (site scope only) using heuristics
                summary_snippet = ""
                try:
                    scope_val = "site" if row.crawl_params else "page"
                except Exception:
                    scope_val = "page"
                if scope_val == "site":
                    desc = ""
                    og_desc = ""
                    try:
                        if isinstance(payload, dict):
                            pg = payload.get("page") or {}
                            desc = (pg.get("description") or "").strip()
                            og_desc = (
                                (pg.get("og") or {}).get("description") or ""
                            ).strip()
                    except Exception:
                        pass

                    def _first_sentence(text: str, limit: int = 160) -> str:
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

                    if desc:
                        summary_snippet = _first_sentence(desc, 160)
                    elif og_desc:
                        summary_snippet = _first_sentence(og_desc, 160)
                    else:
                        try:
                            md = (payload or {}).get("markdown") or ""
                        except Exception:
                            md = ""
                        summary_snippet = _first_sentence(md, 160)

                items.append({
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
                    "is_new": bool(is_new),
                    "email_count": int(email_count or 0),
                    "page_count": int(page_count or 0),
                    "summary_snippet": (summary_snippet if scope_val == "site" else ""),
                })
            # For this branch, we omit keyset prev/next (could add page-based later)
            has_prev = False
            has_next = False
        else:
            # Keyset pagination (recent)
            q = s.query(Crawl).filter(
                Crawl.visibility == "public",
                Crawl.user_id.is_(None),
                Crawl.listed,
            )
            if dom:
                q = q.filter(Crawl.domain == dom)
            if st:
                q = q.filter(Crawl.status == st)
            if has_emails:
                # Filter has_emails post-query (no CrawlEmail table)
                pass

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

            rows_db = q.limit(page_size + 1).all()

            # Compute counts from payload_json (CrawlLink/CrawlEmail tables removed)
            email_counts_map: dict[str, int] = {}
            page_counts_map: dict[str, int] = {}

            for r in rows_db:
                try:
                    p = json.loads(r.payload_json) if r.payload_json else {}
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

            # Build items
            more = len(rows_db) > page_size
            rows = rows_db[:page_size]
            for r in rows:
                title = ""
                payload = None
                try:
                    if r.payload_json:
                        payload = json.loads(r.payload_json)
                        if bool(r.crawl_params):
                            pages = payload.get("pages") or []
                            if pages and isinstance(pages, list) and len(pages) > 0:
                                title = (pages[0].get("page") or {}).get("title") or ""
                        else:
                            title = (payload.get("page") or {}).get("title") or ""
                except Exception:
                    payload = None
                    title = ""

                # Compute relative/iso times and "new" flag (2h threshold)
                updated_dt = r.updated_at or datetime.now(UTC)
                try:
                    if updated_dt.tzinfo is None:
                        updated_dt = updated_dt.replace(tzinfo=UTC)
                except Exception:
                    updated_dt = datetime.now(UTC)
                updated_iso = updated_dt.isoformat()
                # Relative time
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

                # Summary snippet (site scope only) using heuristics
                summary_snippet = ""
                try:
                    scope_val = "site" if r.crawl_params else "page"
                except Exception:
                    scope_val = "page"
                if scope_val == "site":
                    desc = ""
                    og_desc = ""
                    try:
                        if r.payload_json:
                            payload = (
                                payload
                                if isinstance(payload, dict)
                                else json.loads(r.payload_json)
                            )
                        else:
                            payload = payload if isinstance(payload, dict) else None
                    except Exception:
                        payload = payload if isinstance(payload, dict) else None
                    try:
                        if isinstance(payload, dict):
                            pg = payload.get("page") or {}
                            desc = (pg.get("description") or "").strip()
                            og_desc = (
                                (pg.get("og") or {}).get("description") or ""
                            ).strip()
                    except Exception:
                        pass

                    def _first_sentence(text: str, limit: int = 160) -> str:
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

                    if desc:
                        summary_snippet = _first_sentence(desc, 160)
                    elif og_desc:
                        summary_snippet = _first_sentence(og_desc, 160)
                    else:
                        try:
                            md = (payload or {}).get("markdown") or ""
                        except Exception:
                            md = ""
                        summary_snippet = _first_sentence(md, 160)

                items.append({
                    "key": r.key,
                    "domain": r.domain,
                    "path": r.path,
                    "query": r.query,
                    "canonical_url": r.canonical_url,
                    "title": title,
                    "status": r.status,
                    "scope": "site" if r.crawl_params else "page",
                    "updated_at": updated_iso,
                    "updated_iso": updated_iso,
                    "updated_relative": updated_relative,
                    "is_new": bool(is_new),
                    "email_count": email_counts_map.get(r.id, 0),
                    "page_count": page_counts_map.get(r.id, 0),
                    "summary_snippet": (summary_snippet if scope_val == "site" else ""),
                })

            # Build prev/next URLs using first/last item cursors
            def _cursor_of(row: Crawl) -> str:
                """Build a stable cursor string for keyset pagination.

                Args:
                    row (Crawl): Crawl row whose updated_at and id are used.

                Returns:
                    str: Cursor of the form "epoch:id".
                """
                ts = int((row.updated_at or datetime.now(UTC)).timestamp())
                return f"{ts}:{row.id}"

            prev_url = None
            next_url = None
            has_prev = False
            has_next = False
            if rows:
                # Ensure display order matches returned items
                rows_for_nav = list(rows)
                if cursor_ts and cursor_id and direction == "prev":
                    # We fetched ASC; reverse for display
                    rows_for_nav = list(reversed(rows_for_nav))
                first = rows_for_nav[0]
                last = rows_for_nav[-1]
                base_params = {}
                if dom:
                    base_params["domain"] = dom
                if st:
                    base_params["status"] = st
                base_params["page_size"] = str(page_size)
                if has_emails:
                    base_params["has_emails"] = "1"
                if srt:
                    base_params["sort"] = srt

                if not (cursor_ts and cursor_id):
                    # Initial page: no "Prev", "Next" only if there are more
                    if more:
                        next_params = dict(base_params)
                        next_params["cursor"] = _cursor_of(last)
                        next_params["dir"] = "next"
                        next_url = "/all?" + urlencode(next_params)
                        has_next = True
                elif direction == "next":
                    # Older than cursor; always allow navigating back to newer via Prev
                    prev_params = dict(base_params)
                    prev_params["cursor"] = _cursor_of(first)
                    prev_params["dir"] = "prev"
                    prev_url = "/all?" + urlencode(prev_params)
                    has_prev = True
                    # Next only if we fetched more than a full page (more older exist)
                    if more:
                        next_params = dict(base_params)
                        next_params["cursor"] = _cursor_of(last)
                        next_params["dir"] = "next"
                        next_url = "/all?" + urlencode(next_params)
                        has_next = True
                else:  # direction == "prev"
                    # Newer than cursor; always allow navigating to older via Next
                    next_params = dict(base_params)
                    next_params["cursor"] = _cursor_of(last)
                    next_params["dir"] = "next"
                    next_url = "/all?" + urlencode(next_params)
                    has_next = True
                    # Prev only if we fetched more than a full page (more newer exist)
                    if more:
                        prev_params = dict(base_params)
                        prev_params["cursor"] = _cursor_of(first)
                        prev_params["dir"] = "prev"
                        prev_url = "/all?" + urlencode(prev_params)
                        has_prev = True

        # Trending section removed to avoid duplication on the All page.

    # SEO
    site_name = os.getenv("SITE_NAME", "MeshWeave")
    title_bits = ["Public AI search analyses"]
    if dom:
        title_bits.append(f"for {dom}")
    if st:
        title_bits.append(f"(status {st})")
    if srt and srt != "recent":
        title_bits.append(f"— sorted by {srt}")
    page_title = " ".join(title_bits) + f" — {site_name}"

    if dom and st:
        meta_description = (
            f"Explore AEO & GEO scores for {dom} with status {st}. "
            "See how this site performs for AI search."
        )
    elif dom:
        meta_description = (
            f"Explore AEO & GEO scores for {dom}. See how this site "
            "performs across the factors AI engines care about."
        )
    elif st:
        meta_description = (
            f"Browse public AI search analyses filtered by status {st}. "
            "See AEO & GEO scores from the community."
        )
    else:
        meta_description = (
            "Explore AEO & GEO scores submitted by the community. "
            "See how sites perform across the factors AI engines care about."
        )

    # Canonical: keep domain/status only; exclude cursor/page_size/sort/has_emails
    canonical_params = {}
    if dom:
        canonical_params["domain"] = dom
    if st:
        canonical_params["status"] = st
    canonical_path = "/all"
    if canonical_params:
        canonical_path = canonical_path + "?" + urlencode(canonical_params)
    abs_page_url = _abs_url(request, canonical_path)

    og_image_url = os.getenv("OG_IMAGE_URL") or None

    # JSON-LD: ItemList of public analyses (LLM-first)
    try:
        list_name = (
            f"Public AI Search Analyses for {dom}"
            if dom
            else "Public AI Search Analyses"
        )
        elements = []
        for it in items:
            try:
                elements.append({
                    "@type": "CreativeWork",
                    "name": f"Analysis for {it.get('domain') or 'site'}",
                    "identifier": it.get("key", ""),
                    "about": (it.get("domain") or "").strip(),
                    "url": _abs_url(request, f"/analysis/{it.get('key', '')}"),
                    "dateModified": str(it.get("updated_at", ""))[:19],  # ISO-like
                    "keywords": ["AEO", "GEO", "AI search", "optimization"],
                })
            except Exception:
                continue
        json_ld_dict = {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": list_name,
            "itemListElement": elements,
        }
        json_ld = json.dumps(json_ld_dict)
    except Exception:
        json_ld = None

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
            # SEO
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
            "json_ld": json_ld,
        },
    )
