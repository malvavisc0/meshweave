"""Sitemap regressions for the agent-facing surfaces.

Pins the W18 bug: rows that lost their public key (retired) must never
emit ``/analysis/None`` entries in the public sitemap.
"""

from __future__ import annotations

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "sitemap.db"))

import pytest  # noqa: E402, F401
from api_fixtures import crawl_row, user_with_key  # noqa: E402
from fastapi_stub import install_fastapi_stub, install_prometheus_stub  # noqa: E402

install_prometheus_stub()
install_fastapi_stub()


class _FakeRequest:
    headers: dict = {"host": "example.test"}
    base_url = "http://example.test/"
    state = type("S", (), {})()
    app = type("A", (), {"state": type("S", (), {})()})()
    url = type("U", (), {"scheme": "http"})()


def test_sitemap_never_lists_keyless_rows(sessions, monkeypatch):
    """W18: a retired (keyless) row must not emit /analysis/None."""
    from fastapi_stub import load_api_v1_module

    load_api_v1_module()
    api_mod = sys.modules["webapp.routers.api"]

    class _CaptureResponse:
        def __init__(self, content=None, **kwargs):
            self.content = content

    monkeypatch.setattr(api_mod, "Response", _CaptureResponse)

    factory = sessions[1]
    uid, _ = user_with_key(factory, "a@b.c")
    public_id = crawl_row(factory, user_id=uid, domain="pub.example")
    keyless_id = crawl_row(factory, user_id=uid, domain="retired.example")

    with factory() as s:
        from webapp.models import Crawl

        public = s.get(Crawl, public_id)
        keyless = s.get(Crawl, keyless_id)
        assert public is not None and keyless is not None
        public.visibility = "public"
        public.listed = True
        public.key = "pubkey1"
        # A retired row keeps visibility but loses its key.
        keyless.visibility = "public"
        keyless.listed = True
        keyless.key = None
        s.commit()

    resp = __import__("asyncio").run(api_mod.sitemap_xml(_FakeRequest()))
    body = resp.content
    assert "None" not in body
    assert "/analysis/pubkey1" in body
    # XML must stay well-formed.
    assert ET.fromstring(body).tag.endswith("urlset")
