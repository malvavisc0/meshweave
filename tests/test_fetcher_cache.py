from pathlib import Path

import pytest

from meshweave.crawling.fetcher import (
    BrowserSession,
    _cache_path,
    get_rendered_html,
)


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_html(tmp_path: Path):
    """Pre-seed cache and verify get_rendered_html returns it."""
    url = "https://example.com/page"
    cache_dir = str(tmp_path)

    params = {
        "wait_until": "domcontentloaded",
        "progressive_scroll": False,
        "timeout": 30.0,
    }
    cp = _cache_path(cache_dir, url, params)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cached_html = "<html><body>Cached</body></html>"
    cp.write_text(cached_html, encoding="utf-8")

    async with BrowserSession() as session:
        html, metrics = await get_rendered_html(
            url,
            session=session,
            wait_until="domcontentloaded",
            timeout=30.0,
            progressive_scroll=False,
            return_metrics=True,
            cache_dir=cache_dir,
        )

    assert html == cached_html
    assert metrics.cache_hit is True
    assert metrics.content_length == len(cached_html)


@pytest.mark.asyncio
async def test_different_params_different_cache(
    tmp_path: Path,
):
    """Different progressive_scroll values produce
    different cache keys."""
    url = "https://example.com/page"
    cache_dir = str(tmp_path)

    params_a = {
        "wait_until": "domcontentloaded",
        "progressive_scroll": False,
        "timeout": 30.0,
    }
    params_b = {
        "wait_until": "domcontentloaded",
        "progressive_scroll": True,
        "timeout": 30.0,
    }
    cp_a = _cache_path(cache_dir, url, params_a)
    cp_b = _cache_path(cache_dir, url, params_b)

    assert cp_a != cp_b
