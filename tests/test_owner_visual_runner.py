"""Tests for the owner form runner, ``analysis_visual.run_visual_check``.

The endpoint is exercised directly under the analysis-router stub
harness: ownership, sessions, and redirects are patched; the capture is
monkeypatched at the router seam — no browser runs in tests. Pins the
claim lifecycle (store clears the claim, every failure path releases
it) and the SSRF re-validation the API surface applies.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from types import SimpleNamespace

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "runner.db"))

from api_fixtures import (  # noqa: E402
    bearer_request,
    crawl_row,
    fake_audit,
    user_with_key,
)
from fastapi_stub import load_api_v1_module  # noqa: E402

load_api_v1_module()

import webapp.services.visual_audit as visual_audit_mod  # noqa: E402


def _ensure_runtime_stubs() -> None:
    """Starlette/webapp.infra stubs the analysis router chain needs."""
    if "starlette" not in sys.modules:
        starlette = types.ModuleType("starlette")
        status = types.ModuleType("starlette.status")
        for name, code in (
            ("HTTP_401_UNAUTHORIZED", 401),
            ("HTTP_403_FORBIDDEN", 403),
            ("HTTP_404_NOT_FOUND", 404),
            ("HTTP_429_TOO_MANY_REQUESTS", 429),
            ("HTTP_503_SERVICE_UNAVAILABLE", 503),
        ):
            setattr(status, name, code)
        middleware = types.ModuleType("starlette.middleware")
        base = types.ModuleType("starlette.middleware.base")
        base.BaseHTTPMiddleware = type("BaseHTTPMiddleware", (), {})
        starlette.status = status
        starlette.middleware = middleware
        middleware.base = base
        sys.modules["starlette"] = starlette
        sys.modules["starlette.status"] = status
        sys.modules["starlette.middleware"] = middleware
        sys.modules["starlette.middleware.base"] = base
    if "webapp.infra" not in sys.modules:
        infra = types.ModuleType("webapp.infra")
        infra.templates = SimpleNamespace(TemplateResponse=lambda *a, **k: None)
        sys.modules["webapp.infra"] = infra


def _load_owner_runner():
    """Import the analysis router chain and return (analysis, visual)."""
    analysis = sys.modules.get("webapp.routers.analysis")
    if analysis is None:
        import importlib.util
        from pathlib import Path

        _ensure_runtime_stubs()
        module_path = (
            Path(__file__).resolve().parent.parent
            / "webapp"
            / "routers"
            / "analysis.py"
        )
        pkg = sys.modules.get("webapp.routers")
        if pkg is None:
            pkg = types.ModuleType("webapp.routers")
            pkg.__path__ = [str(module_path.parent)]
            sys.modules["webapp.routers"] = pkg
        spec = importlib.util.spec_from_file_location(
            "webapp.routers.analysis", module_path
        )
        assert spec is not None and spec.loader is not None
        analysis = importlib.util.module_from_spec(spec)
        sys.modules["webapp.routers.analysis"] = analysis
        spec.loader.exec_module(analysis)
    return analysis, sys.modules["webapp.routers.analysis_visual"]


class _FakeRedirect:
    """Captures what run_visual_check redirects to (the stub discards it)."""

    def __init__(self, url: str = "", status_code: int = 0) -> None:
        self.url = url
        self.status_code = status_code


class TestOwnerVisualRunner:
    """The owner form endpoint: claim lifecycle, failures, target refusal."""

    def _wire(self, sessions, monkeypatch, row):
        """Load the runner with ownership, sessions, and redirects stubbed."""
        analysis_mod, router_mod = _load_owner_runner()
        import webapp.db as db_mod

        async def own(request, ref):
            return row

        monkeypatch.setattr(analysis_mod, "require_ownership", own)
        monkeypatch.setattr(visual_audit_mod, "get_session", db_mod.get_session)
        monkeypatch.setattr(router_mod, "get_session", db_mod.get_session)
        monkeypatch.setattr(router_mod, "RedirectResponse", _FakeRedirect)
        return router_mod

    def _owned_row(self, factory, uid, cid):
        with factory() as s:
            from webapp.models import Crawl

            row = s.get(Crawl, cid)
            assert row is not None
            return row

    @pytest.mark.asyncio
    async def test_success_stores_and_clears_the_claim(self, sessions, monkeypatch):
        factory = sessions[1]
        uid, _token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
        row = self._owned_row(factory, uid, cid)
        router_mod = self._wire(sessions, monkeypatch, row)

        async def ok(crawl):
            return fake_audit()

        monkeypatch.setattr(router_mod, "run_capture", ok)
        resp = await router_mod.run_visual_check(
            bearer_request(None), cid, csrf_token=None
        )
        assert resp.url.endswith(f"/analysis/{cid}?notice=visual_done")
        with factory() as s:
            from webapp.models_visual import VisualAudit

            stored = s.query(VisualAudit).filter(VisualAudit.crawl_id == cid).one()
            assert stored.screenshot_png is not None
            assert stored.claimed_at is None

    @pytest.mark.asyncio
    async def test_timeout_redirects_and_releases_the_claim(
        self, sessions, monkeypatch
    ):
        factory = sessions[1]
        uid, _token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
        row = self._owned_row(factory, uid, cid)
        router_mod = self._wire(sessions, monkeypatch, row)

        async def boom(crawl):
            raise visual_audit_mod.VisualCaptureTimeout("timed out")

        monkeypatch.setattr(router_mod, "run_capture", boom)
        resp = await router_mod.run_visual_check(
            bearer_request(None), cid, csrf_token=None
        )
        assert resp.url.endswith(f"/analysis/{cid}?notice=visual_timeout")
        assert visual_audit_mod.claim_run(cid) is True  # claim was released

    @pytest.mark.asyncio
    async def test_unexpected_error_releases_the_claim(self, sessions, monkeypatch):
        """Regression: any capture error used to leak the claim for the
        whole cooldown window (a 10-minute owner lockout after a 500)."""
        factory = sessions[1]
        uid, _token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
        row = self._owned_row(factory, uid, cid)
        router_mod = self._wire(sessions, monkeypatch, row)

        async def boom(crawl):
            raise ValueError("saliency model exploded")

        monkeypatch.setattr(router_mod, "run_capture", boom)
        with pytest.raises(ValueError):
            await router_mod.run_visual_check(
                bearer_request(None), cid, csrf_token=None
            )
        assert visual_audit_mod.claim_run(cid) is True  # claim was released

    @pytest.mark.asyncio
    async def test_internal_target_is_refused_without_claiming(
        self, sessions, monkeypatch
    ):
        """Regression: the owner form used to skip the SSRF re-validation
        the API surface applies, aiming the capture at non-public targets."""
        factory = sessions[1]
        uid, _token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="169.254.169.254")
        row = self._owned_row(factory, uid, cid)
        router_mod = self._wire(sessions, monkeypatch, row)

        async def must_not_run(crawl):
            raise AssertionError("capture must not run for a refused target")

        monkeypatch.setattr(router_mod, "run_capture", must_not_run)
        resp = await router_mod.run_visual_check(
            bearer_request(None), cid, csrf_token=None
        )
        assert resp.url.endswith(f"/analysis/{cid}?notice=visual_unavailable")
        with factory() as s:
            from webapp.models_visual import VisualAudit

            assert s.query(VisualAudit).filter(VisualAudit.crawl_id == cid).count() == 0
