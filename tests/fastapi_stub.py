"""Webapp-runtime stubs and the api_v1 module loader, shared by tests.

The webapp runtime (fastapi, prometheus_client, jinja2) is not installed
in the root test environment. These stubs let tests import the pure
response builders inside ``webapp.routers.api_v1`` and
``webapp.routers.api`` without the full application stack.
"""

from __future__ import annotations

import sys
import types


def install_prometheus_stub() -> None:
    """Minimal prometheus_client stub for modules importing webapp.utils.metrics."""
    if "prometheus_client" in sys.modules:
        return
    fake_prom = types.ModuleType("prometheus_client")

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

    fake_prom.Counter = _FakeMetric
    fake_prom.Gauge = _FakeMetric
    fake_prom.Histogram = _FakeMetric
    fake_prom.CONTENT_TYPE_LATEST = "text/plain"
    fake_prom.generate_latest = lambda: b""
    sys.modules["prometheus_client"] = fake_prom


def install_fastapi_stub() -> None:
    """Minimal fastapi stub covering every name the api routers bind.

    Skipped when real fastapi — or an equivalent complete stub — is
    already in ``sys.modules``.
    """
    mod = sys.modules.get("fastapi")
    if mod is not None and getattr(mod, "Depends", None) is not None:
        return
    fastapi = types.ModuleType("fastapi")
    responses = types.ModuleType("fastapi.responses")

    class _Stub:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __call__(self, *a: object, **k: object) -> _Stub:
            return _Stub()

    class _Router(_Stub):
        def get(self, *a: object, **k: object):
            return lambda fn: fn

        def post(self, *a: object, **k: object):
            return lambda fn: fn

        def put(self, *a: object, **k: object):
            return lambda fn: fn

        def patch(self, *a: object, **k: object):
            return lambda fn: fn

        def delete(self, *a: object, **k: object):
            return lambda fn: fn

    class _HTTPException(Exception):
        def __init__(self, status_code: int = 500, detail: object = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _State:
        pass

    class _Request:
        def __init__(self, headers: dict | None = None, body: bytes = b"") -> None:
            self.headers = headers or {}
            self._body = body
            self.state = _State()

        async def json(self) -> dict:
            import json as _json

            return _json.loads(self._body.decode() or "{}")

        async def stream(self):
            # Yields the preloaded body once; json() reads the same
            # retained copy, mirroring FastAPI's receive-cached request.
            if self._body:
                yield self._body

    class _JSONResponse:
        def __init__(self, content=None, status_code: int = 200, **k) -> None:
            self.content = content
            self.status_code = status_code
            self.headers: dict = {}

    class _PlainTextResponse(_JSONResponse):
        def __init__(self, content="", media_type: str = "", **k) -> None:
            super().__init__(content=content, **k)
            self.media_type = media_type

    fastapi.APIRouter = _Router
    fastapi.BackgroundTasks = _Stub
    fastapi.Form = lambda *a, **k: None
    fastapi.HTTPException = _HTTPException
    fastapi.Request = _Request
    fastapi.Response = _Stub
    responses.RedirectResponse = _Stub
    responses.JSONResponse = _JSONResponse
    responses.HTMLResponse = _Stub
    responses.PlainTextResponse = _PlainTextResponse
    responses.Response = _Stub
    fastapi.responses = responses
    fastapi.Depends = lambda *a, **k: None
    fastapi.Query = lambda *a, **k: None
    fastapi.Path = lambda *a, **k: None
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses


_API_V1_MODULE = None


def load_api_v1_module():
    """Import webapp.routers.api_v1 without the routers package __init__.

    The package __init__ imports every router and needs the full webapp
    runtime. Loading the module by file path with a stub package gives the
    api builders directly. Sibling modules it imports from the package
    (api, submissions) are loaded the same way first. The result is cached
    so every test shares one set of module objects.
    """
    global _API_V1_MODULE
    if _API_V1_MODULE is not None:
        return _API_V1_MODULE
    import importlib.util
    from pathlib import Path

    install_prometheus_stub()
    install_fastapi_stub()
    module_path = (
        Path(__file__).resolve().parent.parent / "webapp" / "routers" / "api_v1.py"
    )
    routers_pkg = types.ModuleType("webapp.routers")
    routers_pkg.__path__ = [str(module_path.parent)]
    sys.modules.setdefault("webapp.routers", routers_pkg)

    def _load_sibling(name: str):
        spec = importlib.util.spec_from_file_location(
            f"webapp.routers.{name}", module_path.parent / f"{name}.py"
        )
        sibling = importlib.util.module_from_spec(spec)
        sys.modules[f"webapp.routers.{name}"] = sibling
        spec.loader.exec_module(sibling)
        return sibling

    _load_sibling("api")  # api_v1 imports _bearer_user_id from here
    _load_sibling("submissions")  # api_v1 imports upsert helpers from here
    _API_V1_MODULE = _load_sibling("api_v1")
    return _API_V1_MODULE
