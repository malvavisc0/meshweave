"""Page fetching, BFS crawl engine, and sitemap discovery."""

from .engine import bfs_crawl
from .fetcher import BrowserSession, RenderMetrics, get_rendered_html
from .sitemap import discover_sitemap_urls
from .visual import VisualElement, VisualSnapshot, capture_visual_snapshot

__all__ = [
    "BrowserSession",
    "RenderMetrics",
    "VisualElement",
    "VisualSnapshot",
    "bfs_crawl",
    "capture_visual_snapshot",
    "discover_sitemap_urls",
    "get_rendered_html",
]
