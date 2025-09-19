from pathlib import Path
from typing import cast

import pytest
from markdownify_crawler.fetcher import _DEFAULT_BLOCKED_RESOURCES  # type: ignore
from markdownify_crawler.fetcher import _get_cache_path  # type: ignore
from markdownify_crawler.fetcher import _get_params_hash  # type: ignore
from markdownify_crawler.fetcher import _select_user_agent  # type: ignore
from markdownify_crawler.fetcher import RenderMetrics, get_rendered_html


@pytest.mark.asyncio
async def test_get_rendered_html_cache_key_sensitive_to_headers_and_user_agent(
    tmp_path: Path, monkeypatch
):
    """
    Ensure the expanded cache key discriminates on extra_headers and user_agent
    without launching a real browser by pre-seeding the cache files.
    """
    url = "https://example.com/page"
    device_type = "desktop_chrome"
    viewport = (1280, 720)  # pass explicit viewport to avoid randomness
    ua_default = _select_user_agent(device_type, custom_ua=None, use_default=True)
    ua_custom = "CustomUA/1.0 (+test)"

    # Common params
    wait_until = "domcontentloaded"
    timeout = 30.0
    render_wait = 0.0
    execute_js = None
    progressive_scroll = False
    scroll_for_lazy_load = False
    referer = None
    ignore_https_errors = False
    block_resources = _DEFAULT_BLOCKED_RESOURCES
    stealth_mode = True
    capture_screenshot = False
    intercept_requests = False

    # Precompute cache paths for two scenarios:
    # A) default UA, no headers
    # B) custom UA, with an extra header
    vp = viewport
    cache_dir = str(tmp_path)

    params_A = {
        "wait_until": wait_until,
        "device_type": device_type,
        "viewport": vp,
        "user_agent": ua_default,
        "execute_js": execute_js,
        "progressive_scroll": progressive_scroll,
        "scroll_for_lazy_load": scroll_for_lazy_load,
        "timeout": timeout,
        "render_wait": render_wait,
        "extra_headers": {},
        "referer": referer,
        "ignore_https_errors": ignore_https_errors,
        "block_resources": block_resources,
        "stealth_mode": stealth_mode,
        "capture_screenshot": capture_screenshot,
        "intercept_requests": intercept_requests,
    }
    params_hash_A = _get_params_hash(params_A)
    cache_path_A = _get_cache_path(cache_dir, url, params_hash_A)
    cache_path_A.parent.mkdir(parents=True, exist_ok=True)
    cache_html_A = "<html><body>Cached A</body></html>"
    cache_path_A.write_text(cache_html_A, encoding="utf-8")

    params_B = dict(params_A)
    params_B["user_agent"] = ua_custom
    params_B["extra_headers"] = {"X-Test": "1"}
    params_hash_B = _get_params_hash(params_B)
    cache_path_B = _get_cache_path(cache_dir, url, params_hash_B)
    cache_path_B.parent.mkdir(parents=True, exist_ok=True)
    cache_html_B = "<html><body>Cached B</body></html>"
    cache_path_B.write_text(cache_html_B, encoding="utf-8")

    # Call get_rendered_html for scenario A (should read from cache, not launch browser)
    html_A, metrics_A = await get_rendered_html(
        url,
        wait_until=wait_until,
        timeout=timeout,
        render_wait=render_wait,
        user_agent=None,  # default UA path
        device_type=device_type,
        viewport=vp,
        extra_headers=None,
        wait_for_selector=None,
        referer=referer,
        ignore_https_errors=ignore_https_errors,
        block_resources=None,  # function defaults to _DEFAULT_BLOCKED_RESOURCES internally; cache key used our explicit list
        scroll_for_lazy_load=scroll_for_lazy_load,
        progressive_scroll=progressive_scroll,
        stealth_mode=stealth_mode,
        execute_js=execute_js,
        capture_screenshot=capture_screenshot,
        screenshot_path=None,
        return_metrics=True,
        validate_content=None,
        cache_dir=cache_dir,
        intercept_requests=None,
    )
    assert html_A == cache_html_A
    mA = cast(RenderMetrics, metrics_A)
    assert mA.cache_hit is True
    assert mA.content_length == len(cache_html_A)

    # Call get_rendered_html for scenario B (custom UA and header) should read from distinct cache
    html_B, metrics_B = await get_rendered_html(
        url,
        wait_until=wait_until,
        timeout=timeout,
        render_wait=render_wait,
        user_agent=ua_custom,
        device_type=device_type,
        viewport=vp,
        extra_headers={"X-Test": "1"},
        wait_for_selector=None,
        referer=referer,
        ignore_https_errors=ignore_https_errors,
        block_resources=None,
        scroll_for_lazy_load=scroll_for_lazy_load,
        progressive_scroll=progressive_scroll,
        stealth_mode=stealth_mode,
        execute_js=execute_js,
        capture_screenshot=capture_screenshot,
        screenshot_path=None,
        return_metrics=True,
        validate_content=None,
        cache_dir=cache_dir,
        intercept_requests=None,
    )
    assert html_B == cache_html_B
    mB = cast(RenderMetrics, metrics_B)
    assert mB.cache_hit is True
    assert mB.content_length == len(cache_html_B)

    # Sanity: Different hashes and files
    assert params_hash_A != params_hash_B
    assert cache_path_A != cache_path_B
