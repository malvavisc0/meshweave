"""Launch-blocker regression tests: funnel truth and report honesty.

-: a quota-rejected bulk re-submit leaves the previous revision and
  its queued job intact (retire happens only inside the admitted branch).
-: an LLM-down AAX run yields a suppressed (None) composite, not a
  confident score from one renormalized heuristic.
-: the browser export emits report_exported after the ownership gate.
-: record_gate_taken accepts only gate features.
-: a missing secret key refuses to boot outside development.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "ww3.db"))

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

from fastapi_stub import (
    install_fastapi_stub,
    install_prometheus_stub,
    load_api_v1_module,
)

install_prometheus_stub()
install_fastapi_stub()

api_v1 = load_api_v1_module()
api_mod = sys.modules["webapp.routers.api"]
HTTPException = sys.modules["fastapi"].HTTPException
Request = sys.modules["fastapi"].Request

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402
from webapp.models import Base, Crawl, User  # noqa: E402


@pytest.fixture
def sessions():
    """In-memory DB shared with every patched module under test."""
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    @contextmanager
    def committing():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    import webapp.db as db_mod
    import webapp.services.funnel as funnel_mod
    import webapp.utils.diff as diff_mod
    import webapp.utils.quotas as quotas_mod
    from webapp.services import api_usage as usage_mod

    patched = [db_mod, quotas_mod, funnel_mod, diff_mod, api_mod, usage_mod, api_v1]
    originals = {m: m.get_session for m in patched}
    for m in patched:
        m.get_session = committing
    import webapp.utils.url as url_mod

    _orig_getaddrinfo = url_mod.socket.getaddrinfo

    def _fake_getaddrinfo(host, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    url_mod.socket.getaddrinfo = _fake_getaddrinfo
    yield committing
    url_mod.socket.getaddrinfo = _orig_getaddrinfo
    for m, orig in originals.items():
        m.get_session = orig


def _user(factory, email="u@x.c") -> str:
    uid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            User(id=uid, email=email, provider="google", provider_id=str(uuid.uuid4()))
        )
        s.commit()
    return uid


def _bearer_request(token: str, body: dict):
    import json as _json

    return Request(
        headers={"authorization": f"Bearer {token}"},
        body=_json.dumps(body).encode(),
    )


class TestQuotaRejectedResubmitKeepsHistory:
    """: retire/cancel only happens inside the admitted branch."""

    @pytest.mark.asyncio
    async def test_quota_rejection_leaves_latest_and_queue_intact(
        self, sessions, monkeypatch
    ):
        factory = sessions
        uid = _user(factory)
        import hashlib

        token = "mw_test_" + uuid.uuid4().hex
        from webapp.models import ApiKey

        with factory() as s:
            s.add(
                ApiKey(
                    id=str(uuid.uuid4()),
                    user_id=uid,
                    name="t",
                    key_hash=hashlib.sha256(token.encode()).hexdigest(),
                    key_prefix=token[:16],
                )
            )
            s.commit()

        # Existing latest revision with a pending queue job.
        cid = str(uuid.uuid4())
        ts = datetime.now(UTC) - timedelta(hours=3)
        with factory() as s:
            s.add(
                Crawl(
                    id=cid,
                    url="https://quota.com/",
                    domain="quota.com",
                    path="/",
                    query="",
                    canonical_url="https://quota.com/",
                    visibility="private",
                    status="pending",
                    user_id=uid,
                    payload_json=None,
                    scoring_version=SCORING_VERSION,
                    is_latest=True,
                    queue_status="pending",
                    created_at=ts,
                    updated_at=ts,
                )
            )
            s.commit()

        # Force quota rejection: concurrent limit 0.
        monkeypatch.setenv("AUTH_USER_CONCURRENT_JOBS", "0")
        resp = await api_v1.create_analyses(
            _bearer_request(token, {"urls": ["https://quota.com/"], "scope": "page"})
        )
        item = resp.content["items"][0]
        assert item["status"] == "quota"

        with factory() as s:
            row = s.get(Crawl, cid)
            # The previous revision and its queued job survive.
            assert row.is_latest is True
            assert row.queue_status == "pending"


class TestLlmDownComposite:
    """: no confident Actionable score when the AI review failed."""

    def _aax_result(self, failed: bool) -> dict:
        result = {
            "status": "completed",
            "model_id": "test",
            "tests_completed": 0 if failed else 4,
            "tests_skipped": 4 if failed else 0,
            "contactability": {
                "score": 85.0,
                "has_email": True,
                "has_mailto": True,
                "has_contact_page": False,
                "has_contact_point_schema": False,
                "has_social_links": False,
                "email_count": 1,
                "penalties": [],
                "penalty_points": {},
            },
        }
        if failed:
            result["skip_reasons"] = {
                "homepage_comprehension": "Test failed: timeout",
                "meta_optimization": "Test failed: timeout",
                "content_delta": "Test failed: timeout",
                "email_validation": "Test failed: timeout",
            }
        return result

    def test_all_tests_failed_suppresses_composite(self):
        from meshweave.scoring.engine import compute_aax_score

        section = compute_aax_score(self._aax_result(failed=True))
        assert section is not None
        assert section["composite"] is None
        assert section["rating"] is None
        assert section["degraded"] is True

    def test_healthy_run_composites_normally(self):
        from meshweave.scoring.engine import compute_aax_score

        section = compute_aax_score(self._aax_result(failed=False))
        assert section is not None
        assert section["composite"] is not None
        assert section.get("degraded", False) is False


class TestGateTakenAllowlist:
    """: taken events pair with hits from gate features only."""

    def test_non_gate_feature_never_recorded(self, sessions, monkeypatch):
        from webapp.services import funnel as funnel_svc

        uid = _user(sessions)
        funnel_svc.record_gate_taken(uid, nudge="first_call_hint")
        funnel_svc.record_gate_taken(uid, nudge="services_offer")
        funnel_svc.record_gate_taken(uid, nudge="injected_label")
        with sessions() as s:
            from webapp.models import FunnelEvent

            events = (
                s.query(FunnelEvent.event_type).filter(FunnelEvent.user_id == uid).all()
            )
            assert events == []


class TestSecretKeyFailClosed:
    """: outside dev, a missing secret key refuses to boot."""

    def test_missing_key_raises_outside_dev(self, monkeypatch):
        from webapp.utils.config import _get_secret_key

        monkeypatch.delenv("WEBAPP_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("WEBAPP_ENV", "production")
        with pytest.raises(RuntimeError):
            _get_secret_key()

    def test_missing_key_defaults_in_dev(self, monkeypatch):
        from webapp.utils.config import _get_secret_key

        monkeypatch.delenv("WEBAPP_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.setenv("WEBAPP_ENV", "development")
        assert _get_secret_key() == b"dev-secret"


def _ensure_runtime_stubs() -> None:
    """Starlette/webapp.infra stubs analysis.py needs at import time."""
    if "starlette" not in sys.modules:
        starlette = types.ModuleType("starlette")
        starlette_status = types.ModuleType("starlette.status")
        for name, code in (
            ("HTTP_401_UNAUTHORIZED", 401),
            ("HTTP_403_FORBIDDEN", 403),
            ("HTTP_404_NOT_FOUND", 404),
            ("HTTP_429_TOO_MANY_REQUESTS", 429),
            ("HTTP_503_SERVICE_UNAVAILABLE", 503),
        ):
            setattr(starlette_status, name, code)
        starlette_middleware = types.ModuleType("starlette.middleware")
        starlette_base = types.ModuleType("starlette.middleware.base")
        starlette_base.BaseHTTPMiddleware = type("BaseHTTPMiddleware", (), {})
        starlette.status = starlette_status
        starlette.middleware = starlette_middleware
        starlette_middleware.base = starlette_base
        sys.modules["starlette"] = starlette
        sys.modules["starlette.status"] = starlette_status
        sys.modules["starlette.middleware"] = starlette_middleware
        sys.modules["starlette.middleware.base"] = starlette_base
    if "webapp.infra" not in sys.modules:
        infra = types.ModuleType("webapp.infra")
        infra.templates = types.SimpleNamespace(TemplateResponse=lambda *a, **k: None)
        sys.modules["webapp.infra"] = infra


def _load_analysis_module():
    """Import webapp/routers/analysis.py without the routers package __init__."""
    existing = sys.modules.get("webapp.routers.analysis")
    if existing is not None:
        return existing
    import importlib.util
    from pathlib import Path

    _ensure_runtime_stubs()
    module_path = (
        Path(__file__).resolve().parent.parent / "webapp" / "routers" / "analysis.py"
    )
    routers_pkg = sys.modules.get("webapp.routers")
    if routers_pkg is None:
        routers_pkg = types.ModuleType("webapp.routers")
        routers_pkg.__path__ = [str(module_path.parent)]
        sys.modules["webapp.routers"] = routers_pkg
    spec = importlib.util.spec_from_file_location(
        "webapp.routers.analysis", module_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["webapp.routers.analysis"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeExportResponse:
    def __init__(self, content=None, media_type=None, **kwargs: object) -> None:
        self.content = content
        self.media_type = media_type
        self.headers: dict = {}


class TestBrowserExportEmitsEvent:
    """: the browser export emits report_exported after the ownership gate."""

    def _owned_row(self, uid: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id=str(uuid.uuid4()),
            user_id=uid,
            domain="export.example",
            status="succeeded",
            score_snapshot=object(),
            aax_status="completed",
        )

    @pytest.mark.asyncio
    async def test_owner_download_emits_report_exported(self, sessions, monkeypatch):
        analysis_mod = _load_analysis_module()
        uid = _user(sessions)
        row = self._owned_row(uid)

        async def _own_ok(request, ref):
            return row

        monkeypatch.setattr(analysis_mod, "require_ownership", _own_ok)
        monkeypatch.setattr(analysis_mod, "get_session", sessions)
        monkeypatch.setattr(analysis_mod, "Response", _FakeExportResponse)
        monkeypatch.setattr(analysis_mod, "build_export_context", lambda *a, **k: {})
        monkeypatch.setattr(
            analysis_mod, "render_export_markdown", lambda ctx: "# report"
        )

        resp = await analysis_mod.export_analysis(Request(), row.id)
        assert resp.content == "# report"

        from webapp.models import FunnelEvent

        with sessions() as s:
            events = (
                s.query(FunnelEvent)
                .filter(
                    FunnelEvent.user_id == uid,
                    FunnelEvent.event_type == "report_exported",
                )
                .all()
            )
            assert len(events) == 1
            assert events[0].crawl_id == row.id
            assert events[0].payload["format"] == "markdown"

    @pytest.mark.asyncio
    async def test_unauthorized_export_emits_nothing(self, sessions, monkeypatch):
        analysis_mod = _load_analysis_module()
        uid = _user(sessions)
        row = self._owned_row(uid)
        http_exc = analysis_mod.HTTPException

        async def _own_deny(request, ref):
            raise http_exc(status_code=403, detail="Forbidden")

        monkeypatch.setattr(analysis_mod, "require_ownership", _own_deny)
        monkeypatch.setattr(analysis_mod, "get_session", sessions)

        with pytest.raises(http_exc) as excinfo:
            await analysis_mod.export_analysis(Request(), row.id)
        assert excinfo.value.status_code == 404

        from webapp.models import FunnelEvent

        with sessions() as s:
            events = (
                s.query(FunnelEvent)
                .filter(FunnelEvent.event_type == "report_exported")
                .all()
            )
            assert events == []


class TestTrackBeaconNudgePath:
    """D1 regression: the surface clamp must not swallow nudge names."""

    def _beacon_request(self, uid: str):
        req = Request()
        req.state.current_user = types.SimpleNamespace(id=uid)
        return req

    def test_nudge_cta_click_records_taken(self, sessions):
        from webapp.services import funnel as funnel_svc

        uid = _user(sessions)
        funnel_svc.emit_gated_feature_hit(uid, feature="bulk_gate", surface="result")

        out = api_mod._track_beacon(
            self._beacon_request(uid), "nudge_cta_click", "bulk_gate"
        )
        assert out == {"ok": True}

        from webapp.models import FunnelEvent

        with sessions() as s:
            events = (
                s.query(FunnelEvent)
                .filter(
                    FunnelEvent.user_id == uid,
                    FunnelEvent.event_type == "gated_feature_taken",
                )
                .all()
            )
            assert len(events) == 1
            assert events[0].payload["feature"] == "bulk_gate"

    def test_nudge_click_without_prior_hit_is_ignored(self, sessions):
        uid = _user(sessions)
        api_mod._track_beacon(self._beacon_request(uid), "nudge_cta_click", "bulk_gate")

        from webapp.models import FunnelEvent

        with sessions() as s:
            events = (
                s.query(FunnelEvent)
                .filter(FunnelEvent.event_type == "gated_feature_taken")
                .all()
            )
            assert events == []

    def test_non_gate_surface_never_records_taken(self, sessions):
        from webapp.services import funnel as funnel_svc

        uid = _user(sessions)
        funnel_svc.emit_gated_feature_hit(
            uid, feature="save_analysis", surface="result"
        )
        api_mod._track_beacon(
            self._beacon_request(uid), "nudge_cta_click", "first_call_hint"
        )
        api_mod._track_beacon(
            self._beacon_request(uid), "nudge_cta_click", "injected_label"
        )

        from webapp.models import FunnelEvent

        with sessions() as s:
            events = (
                s.query(FunnelEvent)
                .filter(FunnelEvent.event_type == "gated_feature_taken")
                .all()
            )
            assert events == []


class TestApiKeyFirstUseStamp:
    """W9 regression: activation stamps exactly once, only via the stamp path."""

    def _api_key(self, sessions, uid: str) -> str:
        import hashlib

        key_id = str(uuid.uuid4())
        token = "mw_test_" + uuid.uuid4().hex
        from webapp.models import ApiKey

        with sessions() as s:
            s.add(
                ApiKey(
                    id=key_id,
                    user_id=uid,
                    name="t",
                    key_hash=hashlib.sha256(token.encode()).hexdigest(),
                    key_prefix=token[:16],
                )
            )
            s.commit()
        return key_id

    def test_stamp_exactly_once_and_promotes(self, sessions):
        uid = _user(sessions)
        key_id = self._api_key(sessions, uid)

        req = Request()
        req.state.bearer_api_key_id = key_id
        api_mod.stamp_key_first_use(req)
        api_mod.stamp_key_first_use(req)

        from webapp.models import ApiKey, FunnelEvent, FunnelState

        with sessions() as s:
            events = (
                s.query(FunnelEvent)
                .filter(
                    FunnelEvent.user_id == uid,
                    FunnelEvent.event_type == "api_key_first_used",
                )
                .all()
            )
            assert len(events) == 1
            assert s.get(FunnelState, uid).stage == "api_consumer"
            assert s.get(ApiKey, key_id).first_used_at is not None

    def test_no_bearer_key_id_never_stamps(self, sessions):
        uid = _user(sessions)
        key_id = self._api_key(sessions, uid)

        api_mod.stamp_key_first_use(Request())

        from webapp.models import ApiKey, FunnelEvent

        with sessions() as s:
            assert (
                s.query(FunnelEvent)
                .filter(FunnelEvent.event_type == "api_key_first_used")
                .all()
                == []
            )
            assert s.get(ApiKey, key_id).first_used_at is None
