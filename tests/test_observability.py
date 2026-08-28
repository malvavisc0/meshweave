"""Tests for the opt-in Langfuse observability wiring."""

import logging
import sys
import types
from collections.abc import Iterator
from contextlib import nullcontext

import pytest
from _pytest.monkeypatch import MonkeyPatch

import meshweave.ai.observability as obs


class _FakeLangfuseClient:
    """Stand-in for the Langfuse client that records calls."""

    def __init__(self, *, authenticated: bool = True) -> None:
        self.authenticated = authenticated
        self.flushed = 0

    def auth_check(self) -> bool:
        return self.authenticated

    def flush(self) -> None:
        self.flushed += 1


class _BrokenAuthLangfuseClient(_FakeLangfuseClient):
    """Client whose auth_check raises instead of returning False."""

    def auth_check(self) -> bool:
        raise RuntimeError("auth backend unreachable")


def _fake_langfuse_module(client: _FakeLangfuseClient) -> types.ModuleType:
    """Build a fake ``langfuse`` module backed by ``client``.

    ``propagation_calls`` on the returned module records the kwargs each
    ``propagate_attributes`` call received.
    """
    module = types.ModuleType("langfuse")
    setattr(module, "get_client", lambda: client)
    calls: list[dict] = []

    def _propagate_attributes(**kwargs):
        calls.append(kwargs)
        return nullcontext()

    setattr(module, "propagate_attributes", _propagate_attributes)
    setattr(module, "propagation_calls", calls)
    return module


@pytest.fixture(autouse=True)
def _isolated_observability(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """Isolate observability module state around every test.

    Clears Langfuse env vars, and restores the module globals and the
    pydantic-ai ``instrument_all`` class default afterwards so enabling
    tracing in one test cannot leak into another.
    """
    from pydantic_ai import Agent

    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    instrument_default = Agent._instrument_default
    yield
    obs._client = None
    obs._enabled = False
    Agent._instrument_default = instrument_default


def _set_credentials(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


def _install_fake_langfuse(
    monkeypatch: MonkeyPatch, client: _FakeLangfuseClient
) -> types.ModuleType:
    module = _fake_langfuse_module(client)
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return module


def test_disabled_without_credentials() -> None:
    assert obs.enable_langfuse() is False
    assert obs.langfuse_client() is None


def test_disabled_with_partial_credentials(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    assert obs.enable_langfuse() is False

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "   ")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert obs.enable_langfuse() is False
    assert obs.langfuse_client() is None


def test_disabled_when_langfuse_not_installed(
    monkeypatch: MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_credentials(monkeypatch)
    # None in sys.modules makes ``from langfuse import get_client`` fail.
    monkeypatch.setitem(sys.modules, "langfuse", None)

    with caplog.at_level(logging.WARNING):
        assert obs.enable_langfuse() is False

    assert obs.langfuse_client() is None
    assert any("langfuse" in record.message.lower() for record in caplog.records), (
        f"expected a warning about the missing langfuse package, got {caplog.messages}"
    )


def test_enables_and_instruments_agents(monkeypatch: MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    client = _FakeLangfuseClient()
    _install_fake_langfuse(monkeypatch, client)

    assert obs.enable_langfuse() is True
    assert obs.langfuse_client() is client

    from pydantic_ai import Agent

    assert Agent._instrument_default is True


def test_atexit_flush_registered_once(monkeypatch: MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    _install_fake_langfuse(monkeypatch, _FakeLangfuseClient())

    registered = []
    monkeypatch.setattr(obs.atexit, "register", registered.append)

    assert obs.enable_langfuse() is True
    # Repeated calls must not pile up duplicate atexit handlers.
    assert obs.enable_langfuse() is True
    assert registered == [obs.flush]


def test_auth_failure_warns_but_stays_enabled(
    monkeypatch: MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_credentials(monkeypatch)
    client = _FakeLangfuseClient(authenticated=False)
    _install_fake_langfuse(monkeypatch, client)

    with caplog.at_level(logging.WARNING):
        assert obs.enable_langfuse() is True

    assert obs.langfuse_client() is client
    assert any(
        "authentication failed" in record.message.lower() for record in caplog.records
    ), f"expected an authentication warning, got {caplog.messages}"


def test_auth_check_exception_warns_but_stays_enabled(
    monkeypatch: MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_credentials(monkeypatch)
    client = _BrokenAuthLangfuseClient()
    _install_fake_langfuse(monkeypatch, client)

    with caplog.at_level(logging.WARNING):
        assert obs.enable_langfuse() is True

    assert obs.langfuse_client() is client
    assert any(
        "auth check raised" in record.message.lower() for record in caplog.records
    ), f"expected an auth-check warning, got {caplog.messages}"


def test_flush_is_noop_when_disabled() -> None:
    obs.flush()  # must not raise


def test_flush_calls_client(monkeypatch: MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    client = _FakeLangfuseClient()
    _install_fake_langfuse(monkeypatch, client)
    obs.enable_langfuse()

    obs.flush()

    assert client.flushed == 1


def test_trace_attributes_noop_when_disabled() -> None:
    with obs.trace_attributes(user_id="u", session_id="s", tags=["aax"]):
        assert obs.langfuse_client() is None


def test_trace_attributes_propagates_when_enabled(monkeypatch: MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    module = _install_fake_langfuse(monkeypatch, _FakeLangfuseClient())
    obs.enable_langfuse()

    with obs.trace_attributes(user_id="u1", session_id="aax:c1", tags=["aax"]):
        pass

    assert module.propagation_calls == [
        {
            "user_id": "u1",
            "session_id": "aax:c1",
            "tags": ["aax"],
            "metadata": None,
            "version": None,
        }
    ]
