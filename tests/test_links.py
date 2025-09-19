from markdownify_crawler.core import (
    _IGNORED_DOMAINS,  # type: ignore
    _classify_links,
    soup_from_html,
)


def test_classify_links_internal_external_and_root_skip(monkeypatch):
    html = """
    <html><body>
      <a href="/">Home</a>
      <a href="/about">About</a>
      <a href="/api/status">API Status</a>
      <a href="https://example.com/contact/">Contact</a>
      <a href="https://sub.example.com/page">Subdomain Page</a>
      <a href="mailto:hello@example.com">Email</a>
      <a href="javascript:void(0)">JS</a>
    </body></html>
    """
    soup = soup_from_html(html)
    base_url = "https://example.com/index"

    internal, external, metrics = _classify_links(soup, base_url=base_url)

    # Root path "/" should be skipped; "/api/..." path is ignored by default regex
    # "/about" remains; "/contact/" normalizes to "/contact"
    assert "/about" in internal
    assert "/contact" in internal
    assert "/" not in internal
    assert not any(p.startswith("/api/") for p in internal)

    # External: subdomain is treated as external (strict same-domain match)
    assert any(u == "https://sub.example.com/page" for u in external)
    # mailto/javascript should be skipped entirely
    assert all(not u.startswith("mailto:") and "javascript:" not in u for u in external)

    # Basic metrics present
    assert metrics["internal_count"] == len(internal)
    assert metrics["external_count"] == len(external)
    assert metrics["base_domain"] == "example.com"


def test_classify_links_ignored_domains_filtered(monkeypatch):
    # Ensure filtering of ignored domains in external links
    html = """
    <html><body>
      <a href="https://github.com/org/repo">Repo</a>
      <a href="https://docs.github.com/en/some">Docs</a>
      <a href="https://example.org/news">News</a>
    </body></html>
    """
    soup = soup_from_html(html)
    base_url = "https://example.com"

    # Filter is enabled by default; ensure ignore set contains github.com
    # _IGNORED_DOMAINS is a module-level set compiled at import; patch in-place.
    monkeypatch.setenv("MARKDOWNIFY_FILTER_IGNORED_DOMAINS_IN_LINKS", "true")
    _IGNORED_DOMAINS.clear()
    _IGNORED_DOMAINS.update({"github.com"})

    internal, external, _ = _classify_links(soup, base_url=base_url)

    # No internal links expected for this HTML
    assert internal == []

    # External links should exclude github.com and its subdomains when filter is on
    assert all("github.com" not in u and "docs.github.com" not in u for u in external)
    # Non-ignored domains should remain
    assert any("https://example.org/news" == u for u in external)
