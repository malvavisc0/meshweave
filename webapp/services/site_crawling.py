import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

# Reuse crawler primitives for rendering and link extraction
from markdownify_crawler.core import (
    _classify_links,
    _is_ignored_domain,
    _normalize_abs_url,
    _should_ignore_path,
    extract_emails,
    extract_page_meta,
    preprocess_soup,
    render_page,
    soup_from_html,
    to_markdown,
)

from webapp.db import get_session
from webapp.models import Crawl
from webapp.services.persist import clear_crawl_data, persist_page
from webapp.utils.logging import log_audit
from webapp.utils.metrics import job_duration


def _norm_domain_from_url(u: str) -> str:
    """Normalize a URL's netloc for strict same-registrable-domain checks.

    Args:
        u (str): Input URL.

    Returns:
        str: Lowercased netloc without leading 'www.' when present; empty string on error.
    """
    try:
        host = (urlsplit(u or "").netloc or "").lower().strip()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _int_env(name: str, default: int) -> int:
    """Read an integer environment variable with a fallback.

    Args:
        name (str): Environment variable name.
        default (int): Fallback value when unset or invalid.

    Returns:
        int: Parsed integer value or the provided default.
    """
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _limits_from_row(row: Crawl) -> Dict[str, int]:
    """Resolve crawl limits from row.limits_json and env defaults/caps.

    Args:
        row (Crawl): Crawl ORM row whose limits_json may contain requested values.

    Returns:
        Dict[str, int]: Dict with keys 'max_pages', 'max_depth', and 'time_budget_ms'
            after applying defaults and caps.
    """
    defaults = {
        "max_pages": _int_env("AUTH_SITE_MAX_PAGES_DEFAULT", 200),
        "max_depth": _int_env("AUTH_SITE_MAX_DEPTH_DEFAULT", 3),
        "time_budget_ms": _int_env("AUTH_SITE_TIME_BUDGET_MS_DEFAULT", 600_000),
    }
    caps = {
        "max_pages": _int_env("AUTH_SITE_MAX_PAGES_CAP", 500),
        "max_depth": _int_env("AUTH_SITE_MAX_DEPTH_CAP", 5),
        "time_budget_ms": max(
            60_000, _int_env("AUTH_SITE_TIME_BUDGET_MS_CAP", 3_600_000)
        ),  # min 60s
    }
    try:
        req = json.loads(row.limits_json or "{}")
    except Exception:
        req = {}
    lim = {
        "max_pages": int(
            req.get("max_pages", defaults["max_pages"]) or defaults["max_pages"]
        ),
        "max_depth": int(
            req.get("max_depth", defaults["max_depth"]) or defaults["max_depth"]
        ),
        "time_budget_ms": int(
            req.get("time_budget_ms", defaults["time_budget_ms"])
            or defaults["time_budget_ms"]
        ),
    }
    # Apply caps
    lim["max_pages"] = max(1, min(lim["max_pages"], caps["max_pages"]))
    lim["max_depth"] = max(0, min(lim["max_depth"], caps["max_depth"]))
    lim["time_budget_ms"] = max(
        60_000, min(lim["time_budget_ms"], caps["time_budget_ms"])
    )
    return lim


async def run_site_crawl_task(crawl_id: str, force_refresh: bool = False) -> None:
    """Background task to perform a site crawl (BFS) and persist per-URL details.

    Behavior
    - BFS within same domain (based on start page final URL domain).
    - Enforce max_pages (including start), max_depth (0 = only start page), time budget.
    - On time budget exhaustion, status='failed' with error='time_budget_exceeded'.
    - Store per-URL details in payload_json under 'pages' list with summary and limits.

    Args:
        crawl_id (str): Parent crawl row id (scope='site').
        force_refresh (bool): Disable cache on renders when true.

    Returns:
        None: Performs side effects (DB updates and metrics) and does not return a value.
    """
    now = datetime.now(timezone.utc)
    start_url: Optional[str] = None

    # Transition to running atomically
    with get_session() as s:
        row = s.get(Crawl, crawl_id)
        if not row:
            return
        if row.scope != "site":
            # Wrong scope; mark failed
            row.status = "failed"
            row.error = "invalid_scope"
            row.updated_at = now
            return
        start_url = row.url
        updated = (
            s.query(Crawl)
            .filter(
                Crawl.id == crawl_id,
                Crawl.status.in_(("pending", "failed", "succeeded")),
            )
            .update({"status": "running", "updated_at": now})
        )
        if updated == 0:
            return

    started_monotonic = time.monotonic()
    limits = (
        _limits_from_row(row)
        if start_url
        else {"max_pages": 1, "max_depth": 0, "time_budget_ms": 600_000}
    )
    # Persist effective limits so progress API/UI can display totals (max_pages, etc.)
    try:
        # Record a stable start timestamp for progress/ETA calculations (milliseconds since epoch)
        try:
            import time as _time

            limits["started_at_ms"] = int(_time.time() * 1000)
        except Exception:
            pass
        with get_session() as s:
            r = s.get(Crawl, crawl_id)
            if r:
                r.limits_json = json.dumps(limits)
                r.updated_at = datetime.now(timezone.utc)
    except Exception:
        pass
    time_budget_s = max(1.0, float(limits.get("time_budget_ms", 600_000)) / 1000.0)

    started_monotonic_overall = time.monotonic()
    try:
        try:
            log_audit("site_crawl_started", crawl_id=crawl_id)
        except Exception:
            pass

        # Clear any previously persisted links/emails for this crawl (idempotent)
        try:
            clear_crawl_data(crawl_id)
        except Exception:
            pass

        # Render the start page
        pages: List[Dict[str, Any]] = []
        visited: Set[str] = set()
        queue: Deque[Tuple[str, int]] = deque()
        stop_reason = "queue_empty"
        # Aggregate emails across pages
        all_emails_set: Set[str] = set()
        emails_by_url: Dict[str, List[str]] = {}
        # Track sources per (email,url) with modes like 'mailto'|'text'|'obfuscated'
        email_sources_map: Dict[Tuple[str, str], Set[str]] = {}

        # Aggregate links across pages
        all_internal_paths: Set[str] = set()
        all_external_abs: Set[str] = set()

        # Render function wrapper
        async def _fetch(url: str):
            """Render a URL and return the HTML and render metrics.

            Args:
                url (str): Absolute URL to fetch and render.

            Returns:
                Tuple[str, Any]: Tuple of raw HTML string and a render metrics object.
            """
            html, metrics = await render_page(
                url=url,
                cache_dir=(
                    None
                    if (
                        force_refresh
                        or (
                            os.getenv("MARKDOWNIFY_DISABLE_CACHE", "").strip().lower()
                            in ("1", "true", "yes", "on")
                        )
                    )
                    else (os.getenv("MARKDOWNIFY_CACHE_DIR") or "/tmp/markdownify/cache")
                ),
                timeout=30.0,
                progressive=True,
            )
            return html, metrics

        # Helper to add page record
        def _record_page(
            final_url: str,
            meta: Dict[str, Any],
            markdown: str,
            render_metrics: Dict[str, Any],
            extraction_metrics: Dict[str, Any],
            emails_unique: List[str],
            internal_links: List[str],
            external_links: List[str],
        ):
            """Append a normalized page record to the crawl results.

            Args:
                final_url (str): Final URL after redirects.
                meta (Dict[str, Any]): Extracted page metadata (title, description, og, etc.).
                markdown (str): Markdown content of the page.
                render_metrics (Dict[str, Any]): Renderer metrics including status, load time, etc.
                extraction_metrics (Dict[str, Any]): Link extraction metrics.
                emails_unique (List[str]): Unique emails extracted from the page HTML.
                internal_links (List[str]): Internal links on the page.
                external_links (List[str]): External links on the page.

            Returns:
                None
            """
            pages.append(
                {
                    "url": final_url,
                    "page": meta,
                    "markdown": markdown,
                    "metrics": {
                        "render": render_metrics,
                        "extraction": extraction_metrics,
                    },
                    "emails": {"unique": emails_unique},
                    "links": {
                        "internal": internal_links,
                        "external": external_links,
                    },
                }
            )

        # 1) Start page
        html0, m0 = await _fetch(start_url)  # type: ignore
        final0 = str(getattr(m0, "final_url", start_url or "")) or (start_url or "")
        base_domain_val = _norm_domain_from_url(final0)
        soup0 = soup_from_html(html0)
        meta0 = extract_page_meta(soup0)
        # Classify links on original soup, then preprocess for markdown
        internal0, external0, extraction0 = _classify_links(soup0, base_url=final0)
        soup0_pp = preprocess_soup(soup0, base_url=start_url or final0, final_url=final0)
        md0 = to_markdown(soup0_pp)

        # Aggregate links for site-level summary
        try:
            for href in internal0:
                absu = _normalize_abs_url(href, final0)
                if absu:
                    pth = urlsplit(absu).path or "/"
                    if not pth.startswith("/"):
                        pth = "/" + pth
                    all_internal_paths.add(pth)
            for href in external0:
                absu = _normalize_abs_url(href, final0)
                if absu:
                    all_external_abs.add(absu)
        except Exception:
            pass

        # Emails on start page
        emails0_set, src0 = extract_emails(html0, deobfuscate=True)
        emails0 = sorted(list(emails0_set))
        if emails0:
            emails_by_url[final0] = emails0
        all_emails_set |= emails0_set
        # Record sources for start page
        try:
            for s in src0 or []:
                e = (s.get("email") or "").lower()
                mode = s.get("found_as") or "text"
                if e:
                    email_sources_map.setdefault((e, final0), set()).add(mode)
        except Exception:
            pass

        render_metrics0 = {
            "final_url": final0,
            "response_status": int(getattr(m0, "response_status", 0)),
            "network_requests": int(getattr(m0, "network_requests", 0)),
            "content_length": int(getattr(m0, "content_length", 0)),
            "load_time_ms": round(float(getattr(m0, "load_time", 0.0)) * 1000.0, 2),
            "cache_hit": bool(getattr(m0, "cache_hit", False)),
        }
        _record_page(
            final0,
            meta0,
            md0,
            render_metrics0,
            extraction0,
            emails0,
            internal0,
            external0,
        )

        # Persist start page links/emails
        try:
            persist_page(
                crawl_id=crawl_id,
                page_url=final0,
                base_domain=_norm_domain_from_url(final0),
                internal_links=internal0,
                external_links=external0,
                email_sources=src0,
            )
        except Exception:
            pass

        # Heartbeat: bump updated_at after start page processed
        try:
            with get_session() as s:
                hb_row = s.get(Crawl, crawl_id)
                if hb_row:
                    hb_row.updated_at = datetime.now(timezone.utc)
        except Exception:
            pass

        # Seed queue (depth 1) within domain
        visited.add(final0)
        start_domain_ok = final0

        # Strict domain whitelist enforcement based on start page final URL
        allowed_domains = {_norm_domain_from_url(start_domain_ok)}

        for href in internal0:
            absu = _normalize_abs_url(href, final0)
            if not absu:
                continue
            # Strict whitelist: only enqueue links on allowed domains (post-normalization)
            if _norm_domain_from_url(absu) not in allowed_domains:
                continue
            if _should_ignore_path(urlsplit(absu).path or "") or _is_ignored_domain(absu):
                continue
            if absu in visited:
                continue
            queue.append((absu, 1))
            visited.add(absu)

        # 2) BFS
        while queue and len(pages) < limits["max_pages"]:
            # Cooperative cancellation: stop promptly if status changed
            try:
                with get_session() as s:
                    _cur = s.get(Crawl, crawl_id)
                if not _cur:
                    return
                _st = str(getattr(_cur, "status", "")).lower()

                # Only handle explicit user cancellation here
                if _st == "cancelled":
                    # Persist partial results and mark cancelled
                    with get_session() as s:
                        row = s.get(Crawl, crawl_id)
                        if not row:
                            return
                        row.status = "cancelled"
                        row.error = "cancelled_by_user"
                        # Build deduped sources list
                        try:
                            email_sources_list = [
                                {"email": k[0], "url": k[1], "found_as": sorted(list(v))}
                                for (k, v) in email_sources_map.items()
                            ]
                        except Exception:
                            email_sources_list = []
                        row.payload_json = json.dumps(
                            {
                                "scope": "site",
                                "start_url": start_url,
                                "limits": limits,
                                "domain": base_domain_val,
                                "canonical_url": final0,
                                "links": {
                                    "internal": sorted(list(all_internal_paths)),
                                    "external": sorted(list(all_external_abs)),
                                },
                                "metrics": {
                                    "extraction": {
                                        "base_domain": base_domain_val,
                                        "internal_count": len(all_internal_paths),
                                        "external_count": len(all_external_abs),
                                    }
                                },
                                "emails": {
                                    "unique": sorted(all_emails_set),
                                    "by_url": emails_by_url,
                                    "sources": email_sources_list,
                                    "counts": {
                                        "total_unique": len(all_emails_set),
                                        "total_mentions": sum(
                                            len(v) for v in emails_by_url.values()
                                        ),
                                    },
                                },
                                "pages": pages,
                                "summary": {
                                    "visited_count": len(pages),
                                    "reason_stopped": "cancelled",
                                },
                            }
                        )
                        row.updated_at = datetime.now(timezone.utc)
                    try:
                        log_audit("site_crawl_cancelled", crawl_id=crawl_id)
                    except Exception:
                        pass
                    try:
                        job_duration.labels("site", "cancelled").observe(
                            max(0.0, time.monotonic() - started_monotonic_overall)
                        )
                    except Exception:
                        pass
                    return

                # If job already finished (succeeded/failed) or is not running, exit gracefully without overriding status
                if _st in ("succeeded", "failed") or _st != "running":
                    return
            except Exception:
                pass
            # Time budget check
            if (time.monotonic() - started_monotonic) > time_budget_s:
                stop_reason = "time_budget_exceeded"
                break

            url_i, depth_i = queue.popleft()
            if depth_i > limits["max_depth"]:
                stop_reason = "max_depth"
                continue

            try:
                html_i, m_i = await _fetch(url_i)
            except Exception:
                # Skip on fetch failure
                continue

            final_i = str(getattr(m_i, "final_url", url_i)) or url_i

            # Enforce strict whitelist after redirects
            if _norm_domain_from_url(final_i) not in allowed_domains:
                continue

            # Avoid ignored domains/paths
            if _is_ignored_domain(final_i) or _should_ignore_path(
                urlsplit(final_i).path or ""
            ):
                continue

            # Emails
            emails_i_set, src_i = extract_emails(html_i, deobfuscate=True)
            emails_i = sorted(list(emails_i_set))
            if emails_i:
                emails_by_url[final_i] = emails_i
            all_emails_set |= emails_i_set
            # Record sources for this page
            try:
                for s in src_i or []:
                    e = (s.get("email") or "").lower()
                    mode = s.get("found_as") or "text"
                    if e:
                        email_sources_map.setdefault((e, final_i), set()).add(mode)
            except Exception:
                pass

            # Links and markdown
            soup_i = soup_from_html(html_i)
            meta_i = extract_page_meta(soup_i)
            internal_i, external_i, extraction_i = _classify_links(
                soup_i, base_url=final_i
            )
            soup_i_pp = preprocess_soup(soup_i, base_url=final_i, final_url=final_i)
            md_i = to_markdown(soup_i_pp)

            # Aggregate links for site-level summary
            try:
                for href in internal_i:
                    absu2 = _normalize_abs_url(href, final_i)
                    if absu2:
                        p2 = urlsplit(absu2).path or "/"
                        if not p2.startswith("/"):
                            p2 = "/" + p2
                        all_internal_paths.add(p2)
                for href in external_i:
                    absu3 = _normalize_abs_url(href, final_i)
                    if absu3:
                        all_external_abs.add(absu3)
            except Exception:
                pass

            # Record page
            render_metrics_i = {
                "final_url": final_i,
                "response_status": int(getattr(m_i, "response_status", 0)),
                "network_requests": int(getattr(m_i, "network_requests", 0)),
                "content_length": int(getattr(m_i, "content_length", 0)),
                "load_time_ms": round(float(getattr(m_i, "load_time", 0.0)) * 1000.0, 2),
                "cache_hit": bool(getattr(m_i, "cache_hit", False)),
            }
            _record_page(
                final_i,
                meta_i,
                md_i,
                render_metrics_i,
                extraction_i,
                emails_i,
                internal_i,
                external_i,
            )

            # Persist page links/emails
            try:
                persist_page(
                    crawl_id=crawl_id,
                    page_url=final_i,
                    base_domain=_norm_domain_from_url(final_i),
                    internal_links=internal_i,
                    external_links=external_i,
                    email_sources=src_i,
                )
            except Exception:
                pass

            # Heartbeat: bump updated_at after each BFS page
            try:
                with get_session() as s:
                    hb_row = s.get(Crawl, crawl_id)
                    if hb_row:
                        hb_row.updated_at = datetime.now(timezone.utc)
            except Exception:
                pass

            # Enqueue neighbors if capacity remains
            for href2 in internal_i:
                abs2 = _normalize_abs_url(href2, final_i)
                if not abs2:
                    continue
                if _norm_domain_from_url(abs2) not in allowed_domains:
                    continue
                if _should_ignore_path(urlsplit(abs2).path or "") or _is_ignored_domain(
                    abs2
                ):
                    continue
                if abs2 in visited:
                    continue
                # Check overall capacity before enqueuing
                projected_total = len(pages) + len(queue) + 1  # +1 for this neighbor
                if projected_total > limits["max_pages"]:
                    stop_reason = "max_pages"
                    break
                queue.append((abs2, depth_i + 1))
                visited.add(abs2)

        # Decide final status and reason
        if (time.monotonic() - started_monotonic) > time_budget_s:
            # Time budget exceeded -> fail
            with get_session() as s:
                row = s.get(Crawl, crawl_id)
                if not row:
                    return
                row.status = "failed"
                row.error = "time_budget_exceeded"
                # Build deduped sources list
                try:
                    email_sources_list = [
                        {"email": k[0], "url": k[1], "found_as": sorted(list(v))}
                        for (k, v) in email_sources_map.items()
                    ]
                except Exception:
                    email_sources_list = []
                row.payload_json = json.dumps(
                    {
                        "scope": "site",
                        "start_url": start_url,
                        "limits": limits,
                        "domain": base_domain_val,
                        "canonical_url": final0,
                        "links": {
                            "internal": sorted(list(all_internal_paths)),
                            "external": sorted(list(all_external_abs)),
                        },
                        "metrics": {
                            "extraction": {
                                "base_domain": base_domain_val,
                                "internal_count": len(all_internal_paths),
                                "external_count": len(all_external_abs),
                            }
                        },
                        "emails": {
                            "unique": sorted(all_emails_set),
                            "by_url": emails_by_url,
                            "sources": email_sources_list,
                            "counts": {
                                "total_unique": len(all_emails_set),
                                "total_mentions": sum(
                                    len(v) for v in emails_by_url.values()
                                ),
                            },
                        },
                        "pages": pages,
                        "summary": {
                            "visited_count": len(pages),
                            "reason_stopped": "time_budget_exceeded",
                        },
                    }
                )
                row.updated_at = datetime.now(timezone.utc)
            try:
                log_audit("site_crawl_failed_time_budget", crawl_id=crawl_id)
            except Exception:
                pass
            try:
                job_duration.labels("site", "failed").observe(
                    max(0.0, time.monotonic() - started_monotonic_overall)
                )
            except Exception:
                pass
            return

        # Completed within budget -> succeed
        # If queue non-empty but capacity/depth hit, set appropriate reason
        if queue and len(pages) >= limits["max_pages"]:
            stop_reason = "max_pages"
        elif any(d > limits["max_depth"] for _, d in queue):
            stop_reason = "max_depth"
        else:
            stop_reason = "queue_empty"

        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.status = "succeeded"
            row.error = None
            # Build deduped sources list
            try:
                email_sources_list = [
                    {"email": k[0], "url": k[1], "found_as": sorted(list(v))}
                    for (k, v) in email_sources_map.items()
                ]
            except Exception:
                email_sources_list = []
            row.payload_json = json.dumps(
                {
                    "scope": "site",
                    "start_url": start_url,
                    "limits": limits,
                    "domain": base_domain_val,
                    "canonical_url": final0,
                    "links": {
                        "internal": sorted(list(all_internal_paths)),
                        "external": sorted(list(all_external_abs)),
                    },
                    "metrics": {
                        "extraction": {
                            "base_domain": base_domain_val,
                            "internal_count": len(all_internal_paths),
                            "external_count": len(all_external_abs),
                        }
                    },
                    "emails": {
                        "unique": sorted(all_emails_set),
                        "by_url": emails_by_url,
                        "sources": email_sources_list,
                        "counts": {
                            "total_unique": len(all_emails_set),
                            "total_mentions": sum(len(v) for v in emails_by_url.values()),
                        },
                    },
                    "pages": pages,
                    "summary": {
                        "visited_count": len(pages),
                        "reason_stopped": stop_reason,
                    },
                }
            )
            row.updated_at = datetime.now(timezone.utc)
        try:
            log_audit("site_crawl_succeeded", crawl_id=crawl_id)
        except Exception:
            pass
        try:
            job_duration.labels("site", "succeeded").observe(
                max(0.0, time.monotonic() - started_monotonic_overall)
            )
        except Exception:
            pass

    except Exception as e:
        with get_session() as s:
            row = s.get(Crawl, crawl_id)
            if not row:
                return
            row.status = "failed"
            row.error = str(e)
            row.updated_at = datetime.now(timezone.utc)
        try:
            log_audit("site_crawl_failed", crawl_id=crawl_id)
        except Exception:
            pass
        try:
            job_duration.labels("site", "failed").observe(
                max(0.0, time.monotonic() - started_monotonic_overall)
            )
        except Exception:
            pass
