from meshweave.extraction import classify_links, soup_from_html


def test_classify_links_internal_external_and_root_skip():
    html = """
    <html><body>
      <a href="/">Home</a>
      <a href="/about">About</a>
      <a href="/api/status">API Status</a>
      <a href="https://example.com/contact/">Contact</a>
      <a href="https://sub.example.com/page">Sub</a>
      <a href="mailto:hello@example.com">Email</a>
      <a href="javascript:void(0)">JS</a>
    </body></html>
    """
    soup = soup_from_html(html)
    base_url = "https://example.com/index"

    internal, external, metrics = classify_links(soup, base_url=base_url)

    assert "/about" in internal
    assert "/contact" in internal
    assert "/" not in internal
    assert not any(p.startswith("/api/") for p in internal)

    assert any(u == "https://sub.example.com/page" for u in external)
    bad = [u for u in external if "mailto:" in u or "javascript:" in u]
    assert not bad

    assert metrics["internal_count"] == len(internal)
    assert metrics["external_count"] == len(external)
    assert metrics["base_domain"] == "example.com"


def test_classify_links_ignored_domains_filtered():
    html = """
    <html><body>
      <a href="https://github.com/org/repo">Repo</a>
      <a href="https://docs.github.com/en/some">Docs</a>
      <a href="https://example.org/news">News</a>
    </body></html>
    """
    soup = soup_from_html(html)
    base_url = "https://example.com"

    internal, external, _ = classify_links(
        soup,
        base_url=base_url,
        ignored_domains={"github.com"},
    )

    assert internal == []
    assert all("github.com" not in u for u in external)
    assert any("https://example.org/news" == u for u in external)
