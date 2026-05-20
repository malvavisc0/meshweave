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

    fake_async_api.TimeoutError = _FakeTimeoutError  # type: ignore
    fake_async_api.async_playwright = _fake_async_playwright  # type: ignore

    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.async_api"] = fake_async_api


@pytest.fixture(autouse=True)
def _stable_env(monkeypatch):
    """Stabilize environment for deterministic tests."""
    monkeypatch.setenv("MESHWEAVE_DISABLE_CACHE", "true")
    monkeypatch.setenv("SQLITE_PATH", "/tmp/test_app.db")
    yield
