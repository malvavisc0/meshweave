"""Launch-blocker regression tests: abuse-surface fixes (workwave 1).

Each test repros a hole the launch audit found open:

-: the site-form ``url`` field must pass the internal-target rejection
  (a public decoy domain plus a private ``url`` used to send the crawler
  at the cloud-metadata address).
-: proxy headers must only be trusted when WEBAPP_TRUST_PROXY is set —
  a spoofed X-Real-IP used to mint a fresh rate-limit bucket per request.
-: chunked bodies without Content-Length must hit the 413 cap.
-: every route the agent-facing llms.txt files reference must exist.
-: /api/track clamps its surface label to a fixed allowlist.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "ww1.db"))

if "prometheus_client" not in sys.modules:
    _fake_prom = types.ModuleType("prometheus_client")

    class _FakeMetric:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def labels(self, *args: object, **kwargs: object) -> _FakeMetric:
            return self

        def inc(self, *args: object, **kwargs: object) -> None:
            pass

        def observe(self, *args: object, **kwargs: object) -> None:
            pass

        def set(self, *args: object, **kwargs: object) -> None:
            pass

    _fake_prom.Counter = _FakeMetric
    _fake_prom.Gauge = _FakeMetric
    _fake_prom.Histogram = _FakeMetric
    _fake_prom.CONTENT_TYPE_LATEST = "text/plain"
    _fake_prom.generate_latest = lambda: b""
    sys.modules["prometheus_client"] = _fake_prom

from fastapi_stub import install_fastapi_stub, install_prometheus_stub

install_prometheus_stub()
install_fastapi_stub()

HTTPException = sys.modules["fastapi"].HTTPException
Request = sys.modules["fastapi"].Request


def _load_submissions():
    """Import webapp.routers.submissions without the routers package init."""
    module_path = (
        Path(__file__).resolve().parent.parent / "webapp" / "routers" / "submissions.py"
    )
    routers_pkg = sys.modules.get("webapp.routers")
    if routers_pkg is None:
        routers_pkg = types.ModuleType("webapp.routers")
        routers_pkg.__path__ = [str(module_path.parent)]
        sys.modules["webapp.routers"] = routers_pkg
    existing = sys.modules.get("webapp.routers.submissions")
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        "webapp.routers.submissions", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["webapp.routers.submissions"] = module
    spec.loader.exec_module(module)
    return module


submissions = _load_submissions()


class TestSsrfUrlField:
    """: the site-form url field is guard-checked."""

    def test_internal_target_via_url_field_rejected(self):
        # Public decoy domain, private start URL: must 400.
        with pytest.raises(HTTPException) as exc:
            submissions._site_start_url("example.com", "http://169.254.169.254/latest")
        assert exc.value.status_code == 400

    def test_public_url_field_accepted(self, monkeypatch):
        import webapp.utils.url as url_mod

        def _fake(host, *a, **k):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(url_mod.socket, "getaddrinfo", _fake)
        url = submissions._site_start_url("example.com", "https://example.com/pricing")
        assert url == "https://example.com/pricing"

    def test_no_url_field_defaults_to_domain_root(self):
        assert (
            submissions._site_start_url("example.com", None) == "https://example.com/"
        )

    def test_userinfo_decoy_domain_rejected(self):
        # The real host is after the userinfo; a decoy prefix must not
        # widen what the SSRF guard resolves.
        with pytest.raises(HTTPException) as exc:
            submissions._site_start_url(
                "example.com", "http://evil.com@169.254.169.254/latest"
            )
        assert exc.value.status_code == 400

    def test_explicit_port_internal_target_rejected(self):
        with pytest.raises(HTTPException) as exc:
            submissions._site_start_url(
                "example.com", "http://169.254.169.254:80/latest"
            )
        assert exc.value.status_code == 400

    def test_ipv6_literal_target_rejected(self):
        for raw in ("http://[::1]/", "http://[::ffff:169.254.169.254]/"):
            with pytest.raises(HTTPException) as exc:
                submissions._site_start_url("example.com", raw)
            assert exc.value.status_code == 400

    def test_trailing_dot_internal_target_rejected(self):
        with pytest.raises(HTTPException) as exc:
            submissions._site_start_url("example.com", "http://169.254.169.254./latest")
        assert exc.value.status_code == 400

    def test_public_url_with_port_accepted(self, monkeypatch):
        import webapp.utils.url as url_mod

        def _fake(host, *a, **k):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(url_mod.socket, "getaddrinfo", _fake)
        url = submissions._site_start_url(
            "example.com", "https://example.com:8443/pricing"
        )
        assert url == "https://example.com:8443/pricing"


class TestProxyHeaderTrust:
    """: X-Real-IP is only read when trust_proxy is true."""

    def test_spoofed_real_ip_ignored_without_trust(self):
        from webapp.utils.http import _client_ip_from_request

        req = Request(headers={"x-real-ip": "1.2.3.4"})
        # No connection client on the stub request: a spoofed header must
        # not be picked up as the fallback.
        assert _client_ip_from_request(req, trust_proxy=False) == ""

    def test_real_ip_used_with_trust(self):
        from webapp.utils.http import _client_ip_from_request

        req = Request(headers={"x-real-ip": "1.2.3.4"})
        assert _client_ip_from_request(req, trust_proxy=True) == "1.2.3.4"


@pytest.fixture
def sub_db():
    """In-memory DB with get_session patched into the submissions module."""
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import webapp.db as db_mod

    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from webapp.models import Base

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    @contextmanager
    def get_session():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    patched = [db_mod, submissions]
    originals = {m: m.get_session for m in patched}
    for m in patched:
        m.get_session = get_session
    yield get_session
    for m, orig in originals.items():
        m.get_session = orig


def _client_request(headers: dict | None = None, client_host: str = "10.0.0.5"):
    req = Request(headers=headers or {})
    req.cookies = {}
    req.client = types.SimpleNamespace(host=client_host)
    return req


class TestRateLimitSpoofedBuckets:
    """: a spoofed X-Real-IP must not mint fresh rate-limit buckets."""

    def test_spoofed_real_ip_shares_one_bucket(self, sub_db, monkeypatch):
        from webapp.models import Submission

        monkeypatch.setenv("WEBAPP_RATE_LIMIT_MAX", "2")
        monkeypatch.setenv("WEBAPP_RATE_LIMIT_WINDOW_SEC", "60")
        monkeypatch.setenv("WEBAPP_LOG_REQUESTS", "true")
        monkeypatch.delenv("WEBAPP_TRUST_PROXY", raising=False)
        monkeypatch.delenv("WEBAPP_MASK_IP", raising=False)
        outcomes = []
        for spoof in ("9.9.9.9", "8.8.8.8", "7.7.7.7"):
            req = _client_request(headers={"x-real-ip": spoof})
            try:
                submissions._enforce_rate_limit(req, datetime.now(UTC))
            except HTTPException as exc:
                assert exc.status_code == 429
                outcomes.append("429")
                continue
            outcomes.append("ok")
            submissions._maybe_log_submission(
                req,
                str(uuid.uuid4()),
                "spoof.com",
                "https://spoof.com/",
                False,
                False,
            )
        # Three distinct spoofed IPs, one shared bucket: third hit is 429.
        assert outcomes == ["ok", "ok", "429"]
        with sub_db() as s:
            rows = s.query(Submission).all()
        assert [r.client_ip for r in rows] == ["10.0.0.5", "10.0.0.5"]


class TestMaskedSubmissionHeaders:
    """: masked submissions must not persist the raw proxy headers."""

    def test_masked_submission_omits_raw_headers(self, sub_db, monkeypatch):
        from webapp.models import Submission

        monkeypatch.setenv("WEBAPP_MASK_IP", "true")
        monkeypatch.setenv("WEBAPP_LOG_REQUESTS", "true")
        req = _client_request(
            headers={
                "x-forwarded-for": "9.9.9.9",
                "x-real-ip": "8.8.8.8",
                "user-agent": "ua",
            }
        )
        submissions._maybe_log_submission(
            req, str(uuid.uuid4()), "mask.com", "https://mask.com/", False, False
        )
        with sub_db() as s:
            row = s.query(Submission).one()
        assert row.forwarded_for is None
        assert row.x_real_ip is None
        assert row.client_ip is None
        assert row.client_ip_hash

    def test_unmasked_submission_keeps_raw_headers(self, sub_db, monkeypatch):
        from webapp.models import Submission

        monkeypatch.setenv("WEBAPP_MASK_IP", "false")
        monkeypatch.setenv("WEBAPP_LOG_REQUESTS", "true")
        req = _client_request(
            headers={
                "x-forwarded-for": "9.9.9.9",
                "x-real-ip": "8.8.8.8",
                "user-agent": "ua",
            }
        )
        submissions._maybe_log_submission(
            req, str(uuid.uuid4()), "mask.com", "https://mask.com/", False, False
        )
        with sub_db() as s:
            row = s.query(Submission).one()
        assert row.forwarded_for == "9.9.9.9"
        assert row.x_real_ip == "8.8.8.8"


class _StreamedBody:
    """Minimal request double with a chunked stream and no client."""

    def __init__(self, headers: dict | None = None, chunks: tuple[bytes, ...] = ()):
        self.headers = headers or {}
        self._chunks = list(chunks)

    async def stream(self):
        while self._chunks:
            yield self._chunks.pop(0)


class TestBodySizeCap:
    """: chunked bodies without Content-Length are capped too."""

    @pytest.mark.asyncio
    async def test_chunked_oversize_body_413(self, monkeypatch):
        from webapp.utils.body_size import enforce_body_size

        monkeypatch.setenv("WEBAPP_BODY_MAX_BYTES", "1024")
        req = _StreamedBody(chunks=(b"x" * 2048,))
        with pytest.raises(HTTPException) as exc:
            await enforce_body_size(req)
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_small_chunked_body_passes(self, monkeypatch):
        from webapp.utils.body_size import enforce_body_size

        monkeypatch.setenv("WEBAPP_BODY_MAX_BYTES", "1024")
        req = _StreamedBody(chunks=(b"x" * 100,))
        await enforce_body_size(req)  # no raise

    @pytest.mark.asyncio
    async def test_declared_oversize_content_length_413(self, monkeypatch):
        from webapp.utils.body_size import enforce_body_size

        monkeypatch.setenv("WEBAPP_BODY_MAX_BYTES", "1024")
        req = _StreamedBody(headers={"content-length": "2048"})
        with pytest.raises(HTTPException) as exc:
            await enforce_body_size(req)
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_bad_content_length_400(self, monkeypatch):
        from webapp.utils.body_size import enforce_body_size

        monkeypatch.setenv("WEBAPP_BODY_MAX_BYTES", "1024")
        req = _StreamedBody(headers={"content-length": "abc"})
        with pytest.raises(HTTPException) as exc:
            await enforce_body_size(req)
        assert exc.value.status_code == 400


_WELL_KNOWN = (
    Path(__file__).resolve().parent.parent / "webapp" / "static" / ".well-known"
)

_KNOWN_ROUTES = (
    "/",
    "/api/v1/analyses",
    "/api/v1/analyses/{crawl_id}",
    "/api/v1/analyses/{crawl_id}/diff",
    "/api/v1/analyses/{crawl_id}/diff.md",
    "/api/v1/analyses/{crawl_id}/report.md",
    "/api/analysis/public/{key}",
    "/api/status/{crawl_id}",
    "/api/claim/public/{key}",
    "/profile",
    "/analysis/{ref}",
    "/analysis/{ref}/diff",
    "/browse",
    "/methodology",
    "/privacy",
    "/terms",
    "/contact",
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/llms.txt",
    "/.well-known/llms-full.txt",
    "/dashboard",
    "/submit",
)

_ROUTE_TOKEN = r"/(?:[\w.\-]+(?:/\{?[\w.\-]+\}?)*" + ")"
_LINK_ROUTE_RE = re.compile(r"\]\((" + _ROUTE_TOKEN + r")")
_REF_SPAN_RE = re.compile(
    r"`(?:GET |POST |PUT |PATCH |DELETE )?(" + _ROUTE_TOKEN + r")"
    r"(?:#[\w.\-]+)?(?:\[[^\]]*\])?`(?=\s*[().,:\u2013\u2014]|$)"
)


def _path_segments(path: str) -> list[str]:
    return [seg for seg in path.strip("/").split("/") if seg]


def _is_route_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def _route_matches(known: str, ref: str) -> bool:
    """Exact path match with {param} segments allowed on either side."""
    known_segs = _path_segments(known)
    ref_segs = _path_segments(ref)
    return len(known_segs) == len(ref_segs) and all(
        k == r or _is_route_param(k) or _is_route_param(r)
        for k, r in zip(known_segs, ref_segs)
    )


class TestLlmsLinkCheck:
    """: every route referenced by the agent-facing files exists."""

    def _extract_routes(self, text: str) -> set[str]:
        found = {m.group(1) for m in _LINK_ROUTE_RE.finditer(text)}
        found |= {m.group(1) for m in _REF_SPAN_RE.finditer(text)}
        return found

    def test_llms_files_reference_no_dead_routes(self):
        for name in ("llms.txt", "llms-full.txt"):
            text = (_WELL_KNOWN / name).read_text()
            refs = self._extract_routes(text)
            assert refs
            for route in refs:
                # Exact path match: params match any single segment;
                # string prefixes never count.
                path_only = route.split("#")[0].rstrip("/")
                assert any(
                    _route_matches(known, path_only) for known in _KNOWN_ROUTES
                ), f"{name} references unknown route {route}"

    def test_matcher_rejects_dead_and_prefix_loose_links(self):
        assert not any(_route_matches(k, "/api") for k in _KNOWN_ROUTES)
        assert not any(_route_matches(k, "/ap") for k in _KNOWN_ROUTES)
        assert not any(_route_matches(k, "/browseX") for k in _KNOWN_ROUTES)
        assert not any(
            _route_matches(k, "/api/v1/analyses/x/diff.md/extra") for k in _KNOWN_ROUTES
        )
        assert any(_route_matches(k, "/api/status/{id}") for k in _KNOWN_ROUTES)
        assert any(_route_matches(k, "/analysis/{key}") for k in _KNOWN_ROUTES)

    def test_llms_files_do_not_mention_the_dead_routes(self):
        for name in ("llms.txt", "llms-full.txt"):
            text = (_WELL_KNOWN / name).read_text()
            assert "[API Reference](/api)" not in text
            assert "POST /api/submit" not in text
            # The dead HTML page route; the /api/analysis/public/{key}
            # JSON route is real and stays.
            assert "`/analysis/public/{key}`" not in text


class TestTrackSurfaceAllowlist:
    """: /api/track clamps surface to a fixed allowlist."""

    def test_allowlist_is_bounded_and_rejects_arbitrary_labels(self):
        import webapp.routers.api as api_mod

        assert "footer" in api_mod._TRACK_SURFACES
        assert len(api_mod._TRACK_SURFACES) <= 10
        assert "attacker-controlled-label" not in api_mod._TRACK_SURFACES


class TestBodyReplay:
    """: the body cap must leave the body readable downstream.

    Starlette caches what ``stream()`` consumed on ``request._body``;
    both the direct check and the middleware must use that mechanism or
    every capped-route parser sees an empty body (forms lost all
    fields, JSON endpoints 400ed) in the real runtime.
    """

    @pytest.mark.asyncio
    async def test_enforce_body_size_caches_stream_for_json(self, monkeypatch):
        from webapp.utils.body_size import enforce_body_size

        monkeypatch.setenv("WEBAPP_BODY_MAX_BYTES", "1024")

        class _Req:
            headers = {"content-length": "25"}

            async def stream(self):
                yield b'{"urls": ["https://a.com"]}'

        req = _Req()
        await enforce_body_size(req)
        assert req._body == b'{"urls": ["https://a.com"]}'


class TestAuditLogIpTrust:
    """: log_audit derives the IP with the trust-proxy rule.

    The audit log must not record a spoofed X-Forwarded-For when
    WEBAPP_TRUST_PROXY is off — the same rule the rate limiter uses.
    """

    def test_audit_ip_ignores_forwarded_for_without_trust(self, monkeypatch):
        from webapp.utils import logging as logging_mod

        monkeypatch.delenv("WEBAPP_TRUST_PROXY", raising=False)
        req = Request(headers={"x-forwarded-for": "9.9.9.9"})
        ip = logging_mod._client_ip(req)
        assert ip != "9.9.9.9"

    def test_audit_ip_uses_forwarded_for_with_trust(self, monkeypatch):
        from webapp.utils import logging as logging_mod

        monkeypatch.setenv("WEBAPP_TRUST_PROXY", "true")
        req = Request(headers={"x-forwarded-for": "9.9.9.9"})
        assert logging_mod._client_ip(req) == "9.9.9.9"
