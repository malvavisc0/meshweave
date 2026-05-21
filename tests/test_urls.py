"""Tests for meshweave.urls — origin_prefix, same_origin_prefix, should_follow."""

from meshweave.urls import (
    normalize_abs_url,
    origin_prefix,
    same_origin_prefix,
    should_follow,
)

# ---------------------------------------------------------------------------
# origin_prefix
# ---------------------------------------------------------------------------


class TestOriginPrefix:
    def test_root_url_strips_trailing_slash(self):
        """Root URL 'https://example.com/' should return 'https://example.com'."""
        assert origin_prefix("https://example.com/") == "https://example.com"

    def test_root_url_no_trailing_slash(self):
        """Already clean root URL stays unchanged."""
        assert origin_prefix("https://example.com") == "https://example.com"

    def test_subpath_strips_trailing_slash(self):
        assert origin_prefix("https://example.com/blog/") == "https://example.com/blog"

    def test_subpath_no_trailing_slash(self):
        assert origin_prefix("https://example.com/blog") == "https://example.com/blog"

    def test_deep_subpath(self):
        assert (
            origin_prefix("https://example.com/a/b/c/") == "https://example.com/a/b/c"
        )

    def test_empty_string(self):
        result = origin_prefix("")
        # Should not raise; returns empty or minimal
        assert isinstance(result, str)

    def test_scheme_and_netloc_lowercased(self):
        assert origin_prefix("HTTPS://EXAMPLE.COM/") == "https://example.com"


# ---------------------------------------------------------------------------
# same_origin_prefix
# ---------------------------------------------------------------------------


class TestSameOriginPrefix:
    def test_root_origin_accepts_subpath(self):
        """Bug regression: root origin must accept subpath links."""
        origin = origin_prefix("https://pangolin.net/")
        assert same_origin_prefix("https://pangolin.net/product", origin)

    def test_root_origin_accepts_relative_resolved(self):
        origin = origin_prefix("https://example.com/")
        assert same_origin_prefix("https://example.com/about", origin)

    def test_root_origin_rejects_external(self):
        origin = origin_prefix("https://example.com/")
        assert not same_origin_prefix("https://other.com/page", origin)

    def test_subpath_origin_accepts_deeper(self):
        origin = origin_prefix("https://example.com/blog")
        assert same_origin_prefix("https://example.com/blog/post-1", origin)

    def test_subpath_origin_rejects_sibling(self):
        origin = origin_prefix("https://example.com/blog")
        assert not same_origin_prefix("https://example.com/news", origin)

    def test_exact_match(self):
        origin = origin_prefix("https://example.com/")
        assert same_origin_prefix("https://example.com", origin)


# ---------------------------------------------------------------------------
# should_follow — integration
# ---------------------------------------------------------------------------


class TestShouldFollow:
    def test_root_origin_follows_internal_links(self):
        """End-to-end: should_follow accepts internal links from root origin."""
        origin = origin_prefix("https://pangolin.net/")
        links = [
            "/product",
            "/pricing",
            "/news/how-pangolin-works",
            "/downloads",
        ]
        for href in links:
            abs_url = normalize_abs_url(href, "https://pangolin.net/")
            assert should_follow(abs_url, origin), f"should_follow rejected {href}"

    def test_root_origin_rejects_api_paths(self):
        origin = origin_prefix("https://example.com/")
        assert not should_follow("https://example.com/api/status", origin)

    def test_root_origin_rejects_static_assets(self):
        origin = origin_prefix("https://example.com/")
        assert not should_follow("https://example.com/static/app.js", origin)

    def test_root_origin_rejects_external_domain(self):
        origin = origin_prefix("https://example.com/")
        assert not should_follow("https://other.com/page", origin)
