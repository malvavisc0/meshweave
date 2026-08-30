"""Tests for start-page abort and partial-block stripping in core.crawl."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import meshweave.core as core
from meshweave.crawling.blocked import (
    CrawlBlockedError,
    blocked_error,
    find_blocked_pages,
    start_page_blocked_reason,
)


class TestStartPageBlockedReason:
    """The early-abort predicate for the freshly rendered start page."""

    def test_refusal_status_is_decisive(self):
        reason = start_page_blocked_reason(
            status=429, title="Vercel Security Checkpoint", markdown="x"
        )
        assert reason is not None
        assert "429" in reason

    def test_challenge_title_with_thin_markdown(self):
        reason = start_page_blocked_reason(
            status=200, title="Just a moment...", markdown="blocked"
        )
        assert reason is not None
        assert "interstitial" in reason

    def test_challenge_title_with_real_content_not_blocked(self):
        # A captcha-solving service: title matches but the page has
        # real content — not a block.
        reason = start_page_blocked_reason(
            status=200, title="2Captcha — Verification Service", markdown="word " * 60
        )
        assert reason is None

    def test_normal_page_not_blocked(self):
        assert (
            start_page_blocked_reason(
                status=200, title="Acme — Widgets", markdown="word " * 60
            )
            is None
        )

    def test_blocked_error_format(self):
        assert blocked_error("reason") == (
            "Crawl blocked: reason. Retry later or from a different network."
        )
        assert blocked_error("reason").startswith("Crawl blocked:")


class TestFindBlockedPages:
    """Partial-block detection over crawled markdowns."""

    def _md(self, title: str, markdown: str) -> dict:
        return {"page": {"title": title}, "markdown": markdown}

    def test_challenge_subpage_detected(self):
        markdowns = {
            "https://x.com/": self._md("Acme — Widgets", "word " * 60),
            "https://x.com/pricing": self._md("Just a moment...", "verifying"),
        }
        assert find_blocked_pages(markdowns) == ["https://x.com/pricing"]

    def test_real_content_pages_kept(self):
        markdowns = {
            "https://x.com/": self._md("Acme — Widgets", "word " * 60),
            "https://x.com/blog/access-denied-explained": self._md(
                "Access Denied Explained", "word " * 200
            ),
        }
        assert find_blocked_pages(markdowns) == []

    def test_empty_and_malformed_entries(self):
        assert find_blocked_pages({}) == []
        assert find_blocked_pages({"https://x.com/": "not-a-dict"}) == []


def _fake_render(html: str, *, status: int = 200, final_url: str = "https://x.com/"):
    async def _render(**kwargs: Any):
        return html, SimpleNamespace(
            response_status=status, final_url=final_url, content_length=len(html)
        )

    return _render


class TestCrawlStartPageAbort:
    """core.crawl must abort before BFS when the start page is blocked."""

    @pytest.mark.asyncio
    async def test_blocked_start_page_raises(self, monkeypatch):
        rendered = _fake_render(
            "<html><title>Vercel Security Checkpoint</title></html>", status=429
        )
        monkeypatch.setattr(core, "get_rendered_html", rendered)
        crawled = []

        async def _spy_bfs(*args: Any, **kwargs: Any):
            crawled.append(True)
            return {
                "visited": [],
                "stop_reason": "queue_empty",
                "seeded": 0,
                "all_emails": set(),
                "emails_by_url": {},
                "email_sources": [],
                "markdowns": {},
            }

        monkeypatch.setattr(core, "bfs_crawl", _spy_bfs)

        with pytest.raises(CrawlBlockedError) as excinfo:
            await core.crawl("https://x.com/", crawl_max_pages=5)

        assert str(excinfo.value).startswith("Crawl blocked:")
        assert "429" in str(excinfo.value)
        # The multi-page BFS must never run for a blocked start page —
        # that is the crawl-budget burn this abort exists to prevent.
        assert crawled == []

    @pytest.mark.asyncio
    async def test_blocked_subpages_stripped_from_payload(self, monkeypatch):
        home_md = "real homepage content " + "word " * 60
        rendered = _fake_render(
            f"<html><title>Acme</title><body>{home_md}</body></html>"
        )
        monkeypatch.setattr(core, "get_rendered_html", rendered)
        monkeypatch.setattr(core, "bfs_crawl", _fake_bfs_with_blocked_subpage)
        monkeypatch.setattr(core, "_fetch_robots_and_llms", _fake_robots, raising=True)
        monkeypatch.setattr(
            core,
            "_discover_sitemap_seeds",
            _fake_sitemap,
        )

        payload = await core.crawl("https://x.com/", crawl_max_pages=5)

        # The challenged sub-page is excluded from markdowns/pages …
        assert "https://x.com/pricing" not in payload["markdowns"]
        assert all(p["url"] != "https://x.com/pricing" for p in payload["pages"])
        # … but stays recorded on the crawl metadata for debuggability.
        assert payload["crawl"]["blocked_pages"] == ["https://x.com/pricing"]


async def _fake_robots(base, session):
    return {}, {}


async def _fake_sitemap(origin, origin_pfx, robots_sitemaps, session, max_urls):
    return [], {
        "used": False,
        "sources": [],
        "urls_seeded": 0,
        "discovered": 0,
    }


def _fake_bfs_with_blocked_subpage(*args: Any, **kwargs: Any):
    async def _run(*a: Any, **kw: Any):
        return {
            "visited": ["https://x.com/", "https://x.com/pricing"],
            "stop_reason": "queue_empty",
            "seeded": 0,
            "all_emails": set(),
            "emails_by_url": {},
            "email_sources": [],
            "markdowns": {
                "https://x.com/pricing": {
                    "page": {"title": "Just a moment..."},
                    "markdown": "verifying you are human",
                    "headings": {},
                    "content_metrics": {},
                }
            },
        }

    return _run()
