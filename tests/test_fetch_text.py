"""Regression tests for browser plain-text unwrapping.

Headless browsers (LightPanda via CDP) wrap ``text/plain`` and XML
responses in a minimal HTML document with the payload entity-escaped
inside ``<pre>``.  ``fetch_text`` must unwrap it exactly so that
llms.txt detection and sitemap XML parsing work.
"""

from meshweave.crawling.fetcher import _unwrap_browser_plaintext

# Captured from LightPanda rendering /.well-known/llms.txt over CDP.
LIGHTPANDA_LLMS_WRAPPER = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
    "<body><pre># MeshWeave\n\n"
    "&gt; AI visibility risk analysis for citation, discovery, and agent trust.\n\n"
    "Key pages: https://meshweaveai.com/ &amp; /browse\n"
    "</pre></body></html>"
)

LIGHTPANDA_ROBOTS_WRAPPER = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
    "<body><pre>User-agent: *\nAllow: /\nDisallow: /api/\n"
    "Sitemap: http://meshweave:8080/sitemap.xml\n"
    "</pre></body></html>"
)

LIGHTPANDA_SITEMAP_WRAPPER = (
    "<!DOCTYPE html><html><head></head><body><pre>"
    '&lt;?xml version="1.0" encoding="UTF-8"?&gt;\n'
    '&lt;urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"&gt;\n'
    "&lt;url&gt;&lt;loc&gt;http://meshweave:8080/&lt;/loc&gt;&lt;/url&gt;\n"
    "&lt;/urlset&gt;\n"
    "</pre></body></html>"
)


def test_unwrap_llms_txt_payload_unescaped():
    text = _unwrap_browser_plaintext(LIGHTPANDA_LLMS_WRAPPER)
    assert text.startswith("# MeshWeave")
    assert "> AI visibility risk analysis" in text  # &gt; unescaped
    assert "&amp;" not in text  # &amp; unescaped
    assert "<pre>" not in text
    assert "<html>" not in text


def test_unwrap_robots_txt_payload():
    text = _unwrap_browser_plaintext(LIGHTPANDA_ROBOTS_WRAPPER)
    assert text.splitlines()[0] == "User-agent: *"
    assert "Sitemap: http://meshweave:8080/sitemap.xml" in text


def test_unwrap_sitemap_xml_is_parseable():
    import xml.etree.ElementTree as ET

    text = _unwrap_browser_plaintext(LIGHTPANDA_SITEMAP_WRAPPER)
    root = ET.fromstring(text)
    locs = [el.text for el in root.iter() if el.tag.endswith("loc")]
    assert locs == ["http://meshweave:8080/"]


def test_unwrap_real_html_page_unchanged():
    html = "<!DOCTYPE html><html><head><title>x</title></head><body><h1>Hi</h1></body></html>"
    text = _unwrap_browser_plaintext(html)
    # Real HTML pages are not the plain-text wrapper; the legacy fallback
    # strips wrapper tags but preserves content.
    assert "Hi" in text
    assert "<html>" not in text
    assert "<body>" not in text


def test_unwrap_empty_and_plain_strings():
    assert _unwrap_browser_plaintext("") == ""
    assert _unwrap_browser_plaintext("plain text") == "plain text"
