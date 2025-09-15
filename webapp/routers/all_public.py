import os
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, or_

from webapp.db import get_session
from webapp.infra import templates
from webapp.models import Crawl
from webapp.utils.url import _abs_url

router = APIRouter()


@router.get("/all", response_class=HTMLResponse)
async def view_all(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    cursor: Optional[str] = None,
    dir: Optional[str] = "next",
):
    """Paginated listing of public results with optional filters.

    Query parameters:
      - domain: exact host (lowercase, 'www.' stripped)
      - status: one of {'pending','running','succeeded','failed'}
    """
    # Normalize inputs (keyset pagination)
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

    direction = (dir or "next").lower()
    if direction not in ("next", "prev"):
        direction = "next"

    # Parse cursor of form "epoch:id"
    cursor_ts = None
    cursor_id = None
    if cursor:
        try:
            parts = cursor.split(":", 1)
            cursor_ts = datetime.fromtimestamp(int(parts[0]), tz=timezone.utc)
            cursor_id = parts[1]
        except Exception:
            cursor_ts = None
            cursor_id = None

    # Query rows with keyset filters
    with get_session() as s:
        q = s.query(Crawl).filter(Crawl.visibility == "public")
        if dom:
            q = q.filter(Crawl.domain == dom)
        if st:
            q = q.filter(Crawl.status == st)

        if cursor_ts and cursor_id:
            if direction == "next":
                # Older than cursor (since we order DESC by default)
                q = q.filter(
                    or_(
                        Crawl.updated_at < cursor_ts,
                        and_(Crawl.updated_at == cursor_ts, Crawl.id < cursor_id),
                    )
                )
                q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())
            else:
                # Newer than cursor, fetch ASC then reverse for display
                q = q.filter(
                    or_(
                        Crawl.updated_at > cursor_ts,
                        and_(Crawl.updated_at == cursor_ts, Crawl.id > cursor_id),
                    )
                )
                q = q.order_by(Crawl.updated_at.asc(), Crawl.id.asc())
        else:
            q = q.order_by(Crawl.updated_at.desc(), Crawl.id.desc())

        rows_db: List[Crawl] = q.limit(page_size).all()

    rows = rows_db

    # Build items
    items = []
    for r in rows:
        title = ""
        try:
            if r.payload_json:
                import json

                payload = json.loads(r.payload_json)
                title = (payload.get("page") or {}).get("title") or ""
        except Exception:
            title = ""
        items.append(
            {
                "key": r.key,
                "domain": r.domain,
                "path": r.path,
                "query": r.query,
                "canonical_url": r.canonical_url,
                "title": title,
                "status": r.status,
                "updated_at": (r.updated_at or datetime.now(timezone.utc)).isoformat(),
            }
        )

    # Build prev/next URLs using first/last item cursors
    def _cursor_of(row: Crawl) -> str:
        """Build a stable cursor string for keyset pagination.

        Args:
            row (Crawl): Crawl row whose updated_at and id are used.

        Returns:
            str: Cursor of the form "epoch:id".
        """
        ts = int((row.updated_at or datetime.now(timezone.utc)).timestamp())
        return f"{ts}:{row.id}"

    prev_url = None
    next_url = None
    if rows:
        first = rows[0]
        last = rows[-1]
        base_params = {}
        if dom:
            base_params["domain"] = dom
        if st:
            base_params["status"] = st
        base_params["page_size"] = str(page_size)

        # Prev points to newer items than 'first'
        prev_params = dict(base_params)
        prev_params["cursor"] = _cursor_of(first)
        prev_params["dir"] = "prev"
        prev_url = "/all?" + urlencode(prev_params)

        # Next points to older items than 'last'
        next_params = dict(base_params)
        next_params["cursor"] = _cursor_of(last)
        next_params["dir"] = "next"
        next_url = "/all?" + urlencode(next_params)

    # SEO
    site_name = os.getenv("SITE_NAME", "Markdownify Web App")
    if dom and st:
        page_title = f"All public results for {dom} ({st}) — {site_name}"
        meta_description = (
            f"Browse public results for {dom} with status {st}. Filter and paginate."
        )
    elif dom:
        page_title = f"All public results for {dom} — {site_name}"
        meta_description = f"Browse public results for {dom}. Filter and paginate."
    elif st:
        page_title = f"All public results — status {st} — {site_name}"
        meta_description = (
            f"Browse public results filtered by status {st}. Filter and paginate."
        )
    else:
        page_title = f"All public results — {site_name}"
        meta_description = "Browse all public results. Filter by domain or status, and paginate through the list."

    # Canonical: keep domain/status only; exclude cursor/page_size
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

    return templates.TemplateResponse(
        "all.html",
        {
            "request": request,
            "items": items,
            "page": page,
            "page_size": page_size,
            # UX: we do not compute true has_prev/has_next here; templates tolerate this
            "has_prev": True if prev_url else False,
            "has_next": True if next_url else False,
            "prev_url": prev_url,
            "next_url": next_url,
            "abs_prev_url": _abs_url(request, prev_url) if prev_url else None,
            "abs_next_url": _abs_url(request, next_url) if next_url else None,
            "filter_domain": dom or "",
            "filter_status": st or "",
            # SEO
            "site_name": site_name,
            "page_title": page_title,
            "meta_description": meta_description,
            "abs_page_url": abs_page_url,
            "og_image_url": og_image_url,
        },
    )
