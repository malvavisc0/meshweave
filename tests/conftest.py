import sys
import types

import pytest

# Install fake playwright modules before tests import the package
try:
    import playwright  # noqa: F401
except Exception:
    fake_async_api = types.ModuleType("playwright.async_api")

    class _FakeTimeoutError(Exception):
        pass

    async def _fake_async_playwright():
        class _Ctx:
            async def __aenter__(self):
                class _Chromium:
                    async def launch(self, **kw):
                        raise RuntimeError("Fake playwright: no browser in tests")

                return types.SimpleNamespace(chromium=_Chromium())

            async def __aexit__(self, *a):
                return False

        return _Ctx()

    setattr(fake_async_api, "TimeoutError", _FakeTimeoutError)
    setattr(fake_async_api, "async_playwright", _fake_async_playwright)

    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.async_api"] = fake_async_api


@pytest.fixture(autouse=True)
def _stable_env(monkeypatch):
    """Stabilize environment for deterministic tests."""
    monkeypatch.setenv("MESHWEAVE_DISABLE_CACHE", "true")
    yield


# ── Webapp API tests: shared in-memory DB fixture ─────────────────────

from contextlib import contextmanager  # noqa: E402

from fastapi_stub import load_api_v1_module  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from webapp import models_visual  # noqa: E402, F401  (registers visual_audits)
from webapp.models import Base  # noqa: E402


@pytest.fixture
def sessions():
    """In-memory DB with get_session patched into every module under test.

    The API modules call ``get_session()`` from ``webapp.db``; the fixture
    redirects those references to the in-memory engine so the endpoint code
    and the test share one database.
    """
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

    import sys

    import webapp.db as db_mod
    import webapp.services.crawl_queue as queue_mod
    import webapp.services.funnel as funnel_mod
    import webapp.utils.diff as diff_mod
    import webapp.utils.quotas as quotas_mod

    api_v1 = load_api_v1_module()
    api_mod = sys.modules["webapp.routers.api"]
    api_v1_visual_mod = sys.modules["webapp.routers.api_v1_visual"]
    api_v1_reports_mod = sys.modules["webapp.routers.api_v1_reports"]

    from webapp.services import api_usage as usage_mod

    patched = [
        db_mod,
        quotas_mod,
        queue_mod,
        funnel_mod,
        diff_mod,
        api_mod,
        usage_mod,
        api_v1,
        api_v1_visual_mod,
        api_v1_reports_mod,
    ]
    originals = {m: m.get_session for m in patched}
    for m in patched:
        m.get_session = get_session
    # Deterministic DNS for the SSRF guard: names resolve to a public IP.
    import webapp.utils.url as url_mod

    _orig_getaddrinfo = url_mod.socket.getaddrinfo

    def _fake_getaddrinfo(host, *a, **k):
        if host and host.endswith(".internal"):
            raise OSError("no dns")
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    url_mod.socket.getaddrinfo = _fake_getaddrinfo
    yield engine, factory
    url_mod.socket.getaddrinfo = _orig_getaddrinfo
    for m, orig in originals.items():
        m.get_session = orig
