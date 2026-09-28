"""Cross-version comparison is refused in the browser diff path.

Loads ``webapp/routers/analysis.py`` with runtime stubs (fastapi,
starlette, prometheus_client, and webapp.infra — the same approach
``test_api_v1.py`` uses) and asserts that a pair of runs with different
``scoring_version`` values produces the diff page's refusal error state —
never a diff, never an annotation.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import joinedload, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "xversion.db"))

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


def _install_runtime_stubs() -> None:
    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")
        responses = types.ModuleType("fastapi.responses")

        class _Stub:
            def __init__(self, *a: object, **k: object) -> None:
                pass

        class _Router(_Stub):
            def get(self, *a: object, **k: object):
                return lambda fn: fn

            def post(self, *a: object, **k: object):
                return lambda fn: fn

            def put(self, *a: object, **k: object):
                return lambda fn: fn

        class _HTTPException(Exception):
            def __init__(self, status_code: int = 500, detail: object = None) -> None:
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        fastapi.APIRouter = _Router
        fastapi.Form = lambda *a, **k: None
        fastapi.HTTPException = _HTTPException
        fastapi.Request = _Stub
        fastapi.Response = _Stub
        fastapi.FastAPI = _Stub
        responses.HTMLResponse = _Stub
        responses.RedirectResponse = _Stub
        responses.Response = _Stub
        fastapi.responses = responses
        sys.modules["fastapi"] = fastapi
        sys.modules["fastapi.responses"] = responses

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
        infra.templates = SimpleNamespace(TemplateResponse=lambda *a, **k: None)
        sys.modules["webapp.infra"] = infra


_install_runtime_stubs()

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402
from webapp.models import Base, Crawl, ScoreSnapshot, User  # noqa: E402


def _load_analysis_module():
    """Import webapp/routers/analysis.py without the routers package __init__."""
    module_path = (
        Path(__file__).resolve().parent.parent / "webapp" / "routers" / "analysis.py"
    )
    routers_pkg = types.ModuleType("webapp.routers")
    routers_pkg.__path__ = [str(module_path.parent)]
    sys.modules.setdefault("webapp.routers", routers_pkg)
    spec = importlib.util.spec_from_file_location(
        "webapp.routers.analysis", module_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["webapp.routers.analysis"] = mod
    spec.loader.exec_module(mod)
    return mod


analysis = _load_analysis_module()


@pytest.fixture
def sessions():
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

    import webapp.utils.diff as diff_mod

    originals = {analysis: analysis.get_session, diff_mod: diff_mod.get_session}
    analysis.get_session = get_session
    diff_mod.get_session = get_session
    yield factory
    for mod, orig in originals.items():
        mod.get_session = orig


def _fake_request():
    return SimpleNamespace(
        url=SimpleNamespace(scheme="https"),
        headers={"host": "testserver"},
        app=SimpleNamespace(state=SimpleNamespace()),
    )


def _seed_series(factory, *, old_version: str, new_version: str):
    """Two succeeded revisions of one series with current-shape snapshots."""
    user_id = str(uuid.uuid4())
    rows = []
    with factory() as s:
        s.add(User(id=user_id, email="u@example.com", provider_id=user_id))
        base = datetime(2026, 8, 1, tzinfo=UTC)
        for minutes_ago, version in ((180, old_version), (5, new_version)):
            ts = base + timedelta(minutes=minutes_ago)
            row = Crawl(
                id=str(uuid.uuid4()),
                url="https://example.com/",
                domain="example.com",
                path="/",
                query="",
                canonical_url="https://example.com/",
                visibility="private",
                status="succeeded",
                payload_json={"page": {}},
                user_id=user_id,
                scoring_version=version,
                is_latest=minutes_ago == 5,
                created_at=ts.replace(minute=minutes_ago % 60),
                updated_at=ts,
            )
            s.add(row)
            s.add(
                ScoreSnapshot(
                    crawl_id=row.id,
                    user_id=user_id,
                    domain="example.com",
                    aeo_score=60.0,
                    geo_score=55.0,
                    score_json={
                        "aeo": {
                            "composite": 60.0,
                            "rating": "Partially extractable",
                            "factors": {
                                "schema": {
                                    "score": 50.0,
                                    "weight": 0.3,
                                    "auto_measurable": True,
                                    "raw": {},
                                }
                            },
                        },
                        "geo": {
                            "composite": 55.0,
                            "rating": "Fragmented",
                            "factors": {},
                        },
                        "recommendations": [],
                    },
                    scoring_version=version,
                )
            )
        s.flush()
        rows = (
            s.query(Crawl)
            .options(joinedload(Crawl.score_snapshot))
            .order_by(Crawl.created_at)
            .all()
        )
        for row in rows:
            s.expunge(row)
    return rows[0], rows[1]


class TestBrowserDiffRefusesCrossVersion:
    def test_cross_version_pair_is_refused_not_annotated(self, sessions):
        old_row, new_row = _seed_series(
            sessions, old_version="1.2", new_version=SCORING_VERSION
        )
        ctx = analysis._build_diff_context(_fake_request(), new_row, old_row, True)
        assert ctx["comparison_refused"]
        assert "scoring version" in ctx["comparison_refused"]
        # Refused, not annotated: no diff is built and no notes key exists.
        assert ctx["score_diff"] is None
        assert ctx["findings_diff"] is None
        assert ctx["content_diff"] is None
        assert "comparison_notes" not in ctx

    def test_same_version_pair_diffs_without_notes(self, sessions):
        old_row, new_row = _seed_series(
            sessions, old_version=SCORING_VERSION, new_version=SCORING_VERSION
        )
        ctx = analysis._build_diff_context(_fake_request(), new_row, old_row, True)
        assert ctx["comparison_refused"] is None
        assert ctx["score_diff"] is not None
        assert ctx["findings_diff"] is not None
        assert ctx["content_diff"] is not None
        assert "comparison_notes" not in ctx

    def test_single_run_keeps_empty_state(self, sessions):
        _, new_row = _seed_series(
            sessions, old_version=SCORING_VERSION, new_version=SCORING_VERSION
        )
        ctx = analysis._build_diff_context(_fake_request(), new_row, None, True)
        assert ctx["has_old"] is False
        assert ctx["comparison_refused"] is None
        assert ctx["score_diff"] is None
