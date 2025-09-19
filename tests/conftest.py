import sys
import types

import pytest

# Install fake playwright modules at import time (before tests import the package)
try:
    import playwright  # type: ignore # noqa: F401
except Exception:
    # Build a fake playwright.async_api module with required names
    fake_async_api = types.ModuleType("playwright.async_api")

    class _FakeTimeoutError(Exception):
        pass

    async def _fake_async_playwright():
        # Minimal async context manager that should never be entered when cache hits
        class _Ctx:
            async def __aenter__(self):
                class _Chromium:
                    async def launch(self, **kwargs):
                        raise RuntimeError(
                            "Fake playwright: browser launch should not be called in unit tests"
                        )

                return types.SimpleNamespace(chromium=_Chromium())

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    setattr(fake_async_api, "TimeoutError", _FakeTimeoutError)
    setattr(fake_async_api, "async_playwright", _fake_async_playwright)

    fake_playwright = types.ModuleType("playwright")
    sys.modules["playwright"] = fake_playwright
    sys.modules["playwright.async_api"] = fake_async_api


@pytest.fixture(autouse=True)
def _stable_env(monkeypatch):
    """
    Stabilize environment for deterministic tests.
    """
    # Ensure default ignore patterns behavior is consistent
    monkeypatch.delenv("MARKDOWNIFY_IGNORE_PATHS", raising=False)
    monkeypatch.delenv("MARKDOWNIFY_IGNORE_DOMAINS", raising=False)
    monkeypatch.setenv("MARKDOWNIFY_FILTER_IGNORED_DOMAINS_IN_LINKS", "true")
    # Avoid any cache usage by default in tests
    monkeypatch.setenv("MARKDOWNIFY_DISABLE_CACHE", "true")
    # No slowmo by default
    monkeypatch.delenv("MARKDOWNIFY_DEBUG_SLOWMO_MS", raising=False)
    yield
