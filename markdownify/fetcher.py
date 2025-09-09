"""Utilities for fetching fully-rendered HTML using a real browser engine.

Primary function:
    get_rendered_html(url: str, ...) -> str

This uses Playwright (Chromium) under the hood to load the page with JavaScript
enabled, wait for network activity to settle, and then returns the page's
current HTML (including any DOM mutations done by client-side scripts).

Installation (if not already available):
    pip install playwright
    playwright install --with-deps chromium

Notes:
- Uses safe defaults for containerized environments (no-sandbox flags).
- Provides options to tune wait behavior when pages are highly dynamic.
- Returns the HTML as rendered at the time of capture (page.content()).
- Includes anti-detection features, retry mechanisms, and performance monitoring.
"""

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

# Import Playwright at module level for better error handling
try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:
    raise ImportError(
        "Playwright is required. Install with: pip install playwright && playwright install --with-deps chromium"
    )


@dataclass
class RenderMetrics:
    """Performance and diagnostic metrics for HTML rendering."""

    load_time: float
    network_requests: int
    response_status: int
    final_url: str
    content_length: int
    errors: List[str]
    retries_used: int
    screenshot_path: Optional[str] = None
    cache_hit: bool = False


# Modern User-Agent strings for different devices
_USER_AGENTS = {
    "desktop_chrome": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ],
    "desktop_firefox": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    ],
    "mobile_chrome": [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.0.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    ],
    "mobile_safari": [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    ],
}

_DEFAULT_USER_AGENT = _USER_AGENTS["desktop_chrome"][0]

# Common screen resolutions for stealth mode
_VIEWPORT_SIZES = {
    "desktop": [(1920, 1080), (1366, 768), (1440, 900), (1536, 864), (1280, 720)],
    "mobile": [(375, 667), (414, 896), (390, 844), (360, 640), (412, 915)],
}

# Default resource types to block for faster loading
_DEFAULT_BLOCKED_RESOURCES = ["image"]


def _get_cache_path(cache_dir: str, url: str, params_hash: str) -> Path:
    """Generate cache file path for a URL and parameters."""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    filename = f"{url_hash}_{params_hash}.html"
    return Path(cache_dir) / filename


def _get_params_hash(params: Dict[str, Any]) -> str:
    """Generate hash of parameters for cache key."""
    # Sort params to ensure consistent hashing
    param_str = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(param_str.encode()).hexdigest()[:12]


def _select_user_agent(
    device_type: str, custom_ua: Optional[str], use_default: bool = False
) -> str:
    """Select appropriate user agent string."""
    if custom_ua:
        return custom_ua

    # Use the default user agent if explicitly requested or device_type is default
    if use_default or device_type == "desktop_chrome":
        return _DEFAULT_USER_AGENT

    if device_type not in _USER_AGENTS:
        return _DEFAULT_USER_AGENT

    return random.choice(_USER_AGENTS[device_type])


def _select_viewport(
    device_type: str, custom_viewport: Optional[Tuple[int, int]]
) -> Tuple[int, int]:
    """Select appropriate viewport size."""
    if custom_viewport:
        return custom_viewport

    category = "mobile" if "mobile" in device_type else "desktop"
    return random.choice(_VIEWPORT_SIZES[category])


async def _apply_stealth_measures(page, stealth_mode: bool) -> None:
    """Apply anti-detection measures to the page."""
    if not stealth_mode:
        return

    # Override navigator properties to appear more human-like
    # Add hardware concurrency and other properties for better stealth
    stealth_js = """
    // Hide webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });
    
    // Fake plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    
    // Languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    
    // Fake chrome runtime
    window.chrome = {
        runtime: {},
    };
    
    // Fake permissions
    Object.defineProperty(navigator, 'permissions', {
        get: () => ({
            query: () => Promise.resolve({ state: 'granted' }),
        }),
    });

    // Add hardware concurrency for realism
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
    });
    """

    try:
        await page.add_init_script(stealth_js)
    except Exception as e:
        # Log or handle init script error, but don't crash
        pass


async def _progressive_scroll(page, timeout_ms: int) -> None:
    """Progressively scroll through the page to trigger lazy loading."""
    await page.evaluate(
        """
    (async () => {
        const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
        const scrollHeight = document.body.scrollHeight;
        const viewportHeight = window.innerHeight;
        const steps = Math.min(5, Math.ceil(scrollHeight / viewportHeight)); // Limit steps to 5
        
        for (let i = 0; i <= steps; i++) {
            const scrollTo = (i / steps) * scrollHeight;
            window.scrollTo(0, scrollTo);
            await delay(200); // Reduced wait time
        }
        
        // Scroll back to top
        window.scrollTo(0, 0);
        await delay(100);
    })();
    """
    )

    # Reduced wait time to prevent timeouts
    await page.wait_for_timeout(min(2000, timeout_ms // 4))


async def get_rendered_html(
    url: str,
    *,
    wait_until: (
        Literal["commit", "domcontentloaded", "load", "networkidle"] | None
    ) = "domcontentloaded",
    timeout: float = 30.0,
    render_wait: float = 0.0,
    user_agent: Optional[str] = None,
    device_type: str = "desktop_chrome",
    viewport: Optional[Tuple[int, int]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    wait_for_selector: Optional[str] = None,
    referer: Optional[str] = None,
    ignore_https_errors: bool = True,
    block_resources: Optional[List[str]] = None,
    scroll_for_lazy_load: bool = False,
    progressive_scroll: bool = False,
    stealth_mode: bool = True,
    execute_js: Optional[Union[str, List[str]]] = None,
    capture_screenshot: bool = False,
    screenshot_path: Optional[str] = None,
    return_metrics: bool = False,
    validate_content: Optional[Callable[[str], bool]] = None,
    cache_dir: Optional[str] = None,
    intercept_requests: Optional[Callable[[Any], None]] = None,
) -> Union[str, Tuple[str, RenderMetrics]]:
    """Return HTML of the given URL as rendered in a real browser with advanced features.

    Parameters:
        url: The page URL to load.
        wait_until: One of {"load", "domcontentloaded", "networkidle"}.
        timeout: Max time in seconds for navigation/waits before raising TimeoutError.
        render_wait: Extra time in seconds to wait after initial load.
        user_agent: Custom User-Agent string. If None, uses device_type to select one.
        device_type: Device type for UA selection: "desktop_chrome", "desktop_firefox",
            "mobile_chrome", "mobile_safari". Ignored if user_agent is provided.
        viewport: (width, height) in pixels. If None, auto-selected based on device_type.
        extra_headers: Optional dict of additional HTTP headers.
        wait_for_selector: Optional CSS selector to wait for before capturing HTML.
        referer: Optional HTTP Referer header for the navigation.
        ignore_https_errors: If True, proceed even when encountering HTTPS errors.
        block_resources: List of resource types to block: ["image", "stylesheet", "font",
            "media", "websocket", "other"]. If None, defaults to blocking images only.
        scroll_for_lazy_load: If True, scroll to bottom once to trigger lazy loading.
        progressive_scroll: If True, scroll progressively through page to load all content.
        stealth_mode: If True, apply anti-detection measures (randomized timing, etc.).
        execute_js: Optional JavaScript code string or list of strings to execute.
        max_retries: Number of retries for transient failures.
        retry_delay: Base delay in seconds between retries (with exponential backoff).
        capture_screenshot: If True, save a screenshot of the rendered page.
        screenshot_path: Path to save screenshot. If None, auto-generated.
        return_metrics: If True, return (html, metrics) tuple instead of just html.
        validate_content: Optional function to validate captured content.
        cache_dir: Optional directory to cache responses for faster repeated access.
        intercept_requests: Optional callback to intercept and modify network requests.

    Returns:
        String containing rendered HTML, or (html, metrics) tuple if return_metrics=True.

    Raises:
        ImportError: If Playwright is not installed.
        TimeoutError: If page fails to load within timeout.
        ValueError: If URL is malformed or validation fails.
        RuntimeError: For other browser/navigation issues.

    Example:
        html = get_rendered_html(
            "https://example.com",
            device_type="mobile_chrome",
            progressive_scroll=True,
            stealth_mode=True,
            execute_js="document.querySelector('.load-more').click()",
            return_metrics=True,
        )
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")

    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url must start with http:// or https://")

    if wait_until not in {"load", "domcontentloaded", "networkidle"}:
        raise ValueError(
            'wait_until must be one of {"load", "domcontentloaded", "networkidle"}'
        )

    # Initialize metrics
    metrics = RenderMetrics(
        load_time=0.0,
        network_requests=0,
        response_status=0,
        final_url=url,
        content_length=0,
        errors=[],
        retries_used=0,
    )

    # Check cache first
    cache_hit = False
    cache_path = None
    if cache_dir:
        cache_params = {
            "wait_until": wait_until,
            "device_type": device_type,
            "viewport": viewport,
            "execute_js": execute_js,
            "progressive_scroll": progressive_scroll,
        }
        params_hash = _get_params_hash(cache_params)
        cache_path = _get_cache_path(cache_dir, url, params_hash)

        if cache_path.exists():
            try:
                html = cache_path.read_text(encoding="utf-8")
                metrics.cache_hit = True
                metrics.content_length = len(html)

                if return_metrics:
                    return html, metrics
                return html
            except Exception as e:
                metrics.errors.append(f"Cache read error: {e}")

    # Setup parameters
    timeout_ms = int(max(timeout, 0) * 1000)
    extra_wait_ms = int(max(render_wait, 0) * 1000)
    # Use default UA when no custom device_type is specified and no custom user_agent
    use_default_ua = device_type == "desktop_chrome" and user_agent is None
    ua = _select_user_agent(device_type, user_agent, use_default_ua)

    if block_resources is None:
        block_resources = _DEFAULT_BLOCKED_RESOURCES

    start_time = time.time()
    logger = logging.getLogger(__name__)
    logger.info(f"Starting HTML rendering for URL: {url}")

    try:
        async with async_playwright() as p:
            logger.info("Playwright launched successfully")
            browser = None
            context = None
            try:
                # Launch Chromium with enhanced stealth flags
                browser_args = [
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                ]

                if stealth_mode:
                    browser_args.extend(
                        [
                            "--disable-blink-features=AutomationControlled",
                            "--exclude-switches=enable-automation",
                            "--disable-extensions",
                            "--no-first-run",
                            "--disable-default-apps",
                        ]
                    )

                browser = await p.chromium.launch(
                    headless=True,
                    args=browser_args,
                    slow_mo=50,  # Add small delay between actions for debugging
                    devtools=False,  # Set to True for debugging if needed
                )
                logger.info("Browser launched successfully")

                context = await browser.new_context(
                    user_agent=ua,
                    java_script_enabled=True,
                    extra_http_headers=extra_headers or {},
                    ignore_https_errors=ignore_https_errors,
                    locale="en-US",
                    timezone_id="UTC",
                )
                logger.info("Browser context created successfully")

                page = await context.new_page()
                logger.info("New page created successfully")

                # Apply stealth measures
                await _apply_stealth_measures(page, stealth_mode)

                # Resource blocking
                if block_resources:
                    block_patterns = []
                    for resource_type in block_resources:
                        if resource_type == "image":
                            block_patterns.append("**/*.{png,jpg,jpeg,gif,svg,ico,webp}")
                        elif resource_type == "stylesheet":
                            block_patterns.append("**/*.css")
                        elif resource_type == "font":
                            block_patterns.append("**/*.{woff,woff2,ttf,otf}")
                        elif resource_type == "media":
                            block_patterns.append("**/*.{mp4,mp3,avi,mov}")

                    for pattern in block_patterns:
                        await page.route(pattern, lambda route: route.abort())

                # Network request interception
                if intercept_requests:
                    await page.route("**/*", intercept_requests)

                # Track network requests
                request_count = 0

                def count_requests(request):
                    nonlocal request_count
                    request_count += 1

                page.on("request", count_requests)

                # Navigate to page
                try:
                    response = await page.goto(
                        url,
                        wait_until=wait_until,
                        timeout=timeout_ms,
                        referer=referer,
                    )

                    if response:
                        metrics.response_status = response.status
                        metrics.final_url = response.url
                except PlaywrightTimeoutError as e:
                    metrics.errors.append(f"Navigation timeout: {e}")
                    raise TimeoutError(
                        f"Navigation to {url} timed out after {timeout}s"
                    ) from e

                # Wait for specific selector if requested
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(
                            wait_for_selector, timeout=timeout_ms, state="attached"
                        )
                    except PlaywrightTimeoutError as e:
                        metrics.errors.append(f"Selector wait timeout: {e}")
                        raise TimeoutError(
                            f'Waiting for selector "{wait_for_selector}" timed out after {timeout}s'
                        ) from e

                # Progressive scrolling for lazy loading
                if progressive_scroll:
                    await _progressive_scroll(page, timeout_ms)
                elif scroll_for_lazy_load:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(500)

                # Execute custom JavaScript
                if execute_js:
                    js_list = [execute_js] if isinstance(execute_js, str) else execute_js
                    for js_code in js_list:
                        try:
                            await page.evaluate(js_code)
                            await page.wait_for_timeout(
                                200
                            )  # Reduced wait for JS effects
                        except Exception as e:
                            metrics.errors.append(f"JavaScript execution error: {e}")

                # Additional wait time
                if extra_wait_ms > 0:
                    await page.wait_for_timeout(extra_wait_ms)

                # Capture screenshot if requested
                if capture_screenshot:
                    if not screenshot_path:
                        timestamp = int(time.time())
                        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                        screenshot_path = f"screenshot_{url_hash}_{timestamp}.png"

                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        metrics.screenshot_path = screenshot_path
                    except Exception as e:
                        metrics.errors.append(f"Screenshot capture error: {e}")

                # Capture the fully rendered HTML
                logger.info("Capturing rendered HTML content")
                html = await page.content()
                metrics.content_length = len(html)
                metrics.network_requests = request_count
                metrics.retries_used = 0
                logger.info(
                    f"HTML captured successfully. Content length: {len(html)} characters, Network requests: {request_count}"
                )

                # Validate content if validator provided
                if validate_content and not validate_content(html):
                    raise ValueError("Content validation failed")

                # Cache the result
                if cache_dir and cache_path and not cache_hit:
                    try:
                        Path(cache_dir).mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(html, encoding="utf-8")
                    except Exception as e:
                        metrics.errors.append(f"Cache write error: {e}")

                metrics.load_time = time.time() - start_time
                logger.info(
                    f"HTML rendering completed successfully in {metrics.load_time:.2f}s"
                )

                if return_metrics:
                    return html, metrics
                return html

            finally:
                # Cleanup resources
                try:
                    if context:
                        await context.close()
                except Exception:
                    pass
                try:
                    if browser:
                        await browser.close()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to render HTML for {url}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to render HTML for {url}: {e}") from e
