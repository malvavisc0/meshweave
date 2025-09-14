from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from webapp.db import get_session
from webapp.models import CrawlEmail, CrawlLink
from webapp.utils.url import normalize_domain


def _abs_internal_url(base_domain: str, u: str) -> Tuple[str, str]:
    """
    Compute absolute URL and domain for an internal link string.

    - If u already looks absolute (starts with http/https), keep it and derive domain from it.
    - Else ensure leading '/' and build https://{base_domain}{path}.
    """
    u = (u or "").strip()
    if not u:
        return "", ""
    if u.startswith("http://") or u.startswith("https://"):
        dom = normalize_domain(u)
        return u, dom
    path = u if u.startswith("/") else f"/{u}"
    abs_u = f"https://{base_domain}{path}" if base_domain else path
    return abs_u, base_domain or ""


def clear_crawl_data(crawl_id: str) -> None:
    """
    Delete any persisted links/emails for a crawl id (idempotent).
    """
    with get_session() as s:
        s.query(CrawlLink).filter(CrawlLink.crawl_id == crawl_id).delete(
            synchronize_session=False
        )
        s.query(CrawlEmail).filter(CrawlEmail.crawl_id == crawl_id).delete(
            synchronize_session=False
        )


def persist_page(
    *,
    crawl_id: str,
    page_url: str,
    base_domain: str,
    internal_links: Optional[Sequence[str]] = None,
    external_links: Optional[Sequence[str]] = None,
    email_sources: Optional[Sequence[Dict[str, Any]]] = None,
    emails_unique_fallback: Optional[Sequence[str]] = None,
) -> None:
    """
    Persist links and emails for a single page.

    - Deduplicates rows per (page_url, absolute_url, type) for links.
    - Deduplicates emails per (page_url, email) and merges found_as labels.

    email_sources: items like {"email": str, "url": str, "found_as": str|list[str]}
    """
    internal_links = internal_links or []
    external_links = external_links or []

    # Deduplicate internal/external inputs while preserving order minimally
    def _dedup(seq: Iterable[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for x in seq:
            x = (x or "").strip()
            if not x or x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    internals = _dedup(internal_links)
    externals = _dedup(external_links)

    # Build CrawlLink rows, dedup by (page_url, absolute_url, type)
    link_keys = set()
    link_rows: List[CrawlLink] = []

    for u in internals:
        abs_u, dom = _abs_internal_url(base_domain, u)
        if not abs_u:
            continue
        key = (page_url, abs_u, "internal")
        if key in link_keys:
            continue
        link_keys.add(key)
        link_rows.append(
            CrawlLink(
                crawl_id=crawl_id,
                page_url=page_url,
                url=u,
                absolute_url=abs_u,
                type="internal",
                domain=dom,
            )
        )

    for u in externals:
        u = (u or "").strip()
        if not u:
            continue
        abs_u = u
        dom = normalize_domain(u)
        key = (page_url, abs_u, "external")
        if key in link_keys:
            continue
        link_keys.add(key)
        link_rows.append(
            CrawlLink(
                crawl_id=crawl_id,
                page_url=page_url,
                url=u,
                absolute_url=abs_u,
                type="external",
                domain=dom,
            )
        )

    # Build CrawlEmail rows; aggregate found_as per (page_url, email)
    email_map: Dict[Tuple[str, str], set] = {}

    if email_sources:
        for item in email_sources:
            try:
                em = str(item.get("email", "")).strip().lower()
                if not em:
                    continue
                page = str(item.get("url") or page_url) or page_url
                fa = item.get("found_as")
                if isinstance(fa, list):
                    found_vals = [str(x).strip().lower() for x in fa if str(x).strip()]
                elif isinstance(fa, str):
                    found_vals = [fa.strip().lower()] if fa.strip() else []
                else:
                    found_vals = []
                key = (page, em)
                if key not in email_map:
                    email_map[key] = set()
                email_map[key].update(found_vals)
            except Exception:
                continue
    elif emails_unique_fallback:
        for em in emails_unique_fallback:
            em = (em or "").strip().lower()
            if not em:
                continue
            key = (page_url, em)
            if key not in email_map:
                email_map[key] = set()

    email_rows: List[CrawlEmail] = []
    for (page, em), modes in email_map.items():
        found_as = ",".join(sorted(m for m in modes if m)) or None
        email_rows.append(
            CrawlEmail(
                crawl_id=crawl_id,
                page_url=page,
                email=em,
                found_as=found_as,
            )
        )

    if not link_rows and not email_rows:
        return

    with get_session() as s:
        if link_rows:
            s.bulk_save_objects(link_rows)
        if email_rows:
            s.bulk_save_objects(email_rows)
