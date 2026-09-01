"""Tests for the opt-in Langfuse observability wiring."""

import asyncio
import json
import logging
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

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


def _fake_langfuse_module(
    client: _FakeLangfuseClient,
) -> tuple[types.ModuleType, dict]:
    """Build a fake ``langfuse`` module backed by ``client``.

    ``propagation_calls`` on the returned module records the kwargs each
    ``propagate_attributes`` call received. ``construction_calls`` on the
    module records the kwargs ``Langfuse(...)`` received.
    """
    module = types.ModuleType("langfuse")
    construction: list[dict] = []

    class Langfuse:  # noqa: N801 - mirrors the SDK class name
        def __init__(self, **kwargs) -> None:
            construction.append(kwargs)

        def auth_check(self) -> bool:
            return client.auth_check()

        def flush(self) -> None:
            client.flush()

    setattr(module, "Langfuse", Langfuse)
    calls: list[dict] = []

    def _propagate_attributes(**kwargs):
        calls.append(kwargs)
        return nullcontext()

    setattr(module, "propagate_attributes", _propagate_attributes)
    setattr(module, "propagation_calls", calls)
    setattr(module, "construction_calls", construction)
    return module, construction


def _install_fake_langfuse(
    monkeypatch: MonkeyPatch, client: _FakeLangfuseClient
) -> tuple[types.ModuleType, dict]:
    module, construction = _fake_langfuse_module(client)
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return module, construction


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
    module, construction = _install_fake_langfuse(monkeypatch, client)

    assert obs.enable_langfuse() is True
    assert obs.langfuse_client() is not None

    from pydantic_ai import Agent

    assert Agent._instrument_default is True

    # The client must be constructed with the masking hook registered.
    assert len(construction) == 1
    assert construction[0]["public_key"] == "pk-lf-test"
    assert construction[0]["secret_key"] == "sk-lf-test"
    assert construction[0]["mask_otel_spans"] is obs.mask_otel_spans


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

    assert obs.langfuse_client() is not None
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

    assert obs.langfuse_client() is not None
    assert any(
        "auth check raised" in record.message.lower() for record in caplog.records
    ), f"expected an auth-check warning, got {caplog.messages}"


def test_flush_is_noop_when_disabled() -> None:
    obs.flush()  # must not raise


def test_trace_attributes_noop_when_disabled() -> None:
    with obs.trace_attributes(user_id="u", session_id="s", tags=["aax"]):
        assert obs.langfuse_client() is None


def test_trace_attributes_propagates_when_enabled(monkeypatch: MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    module, _ = _install_fake_langfuse(monkeypatch, _FakeLangfuseClient())
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


def test_trace_attributes_propagates_metadata(
    monkeypatch: MonkeyPatch,
) -> None:
    _set_credentials(monkeypatch)
    module, _ = _install_fake_langfuse(monkeypatch, _FakeLangfuseClient())
    obs.enable_langfuse()

    with obs.trace_attributes(user_id="u1", metadata={"plan": "pro"}):
        pass

    assert module.propagation_calls[0]["user_id"] == "u1"
    assert module.propagation_calls[0]["metadata"] == {"plan": "pro"}


def test_aax_uses_anonymous_id_when_no_authenticated_user(
    monkeypatch: MonkeyPatch,
) -> None:
    from meshweave.ai import analyses

    calls: list[dict] = []

    @contextmanager
    def fake_trace_attributes(**kwargs):
        calls.append(kwargs)
        yield

    async def fake_run(payload: dict) -> dict:
        return payload

    monkeypatch.setattr(analyses, "trace_attributes", fake_trace_attributes)
    monkeypatch.setattr(analyses, "_run_aax_analysis", fake_run)

    result = asyncio.run(
        analyses.run_aax_analysis(
            {"status": "completed"},
            trace_anonymous_user_id="anon_123",
            trace_session_id="aax:c1",
        )
    )

    assert result == {"status": "completed"}
    assert calls == [
        {
            "user_id": "anon_123",
            "session_id": "aax:c1",
            "tags": ["aax"],
            "metadata": None,
        }
    ]


# --- thinking-part masking --------------------------------------------------


def test_strip_thinking_parts_removes_thinking_from_json_strings() -> None:
    value = json.dumps(
        [
            {
                "role": "assistant",
                "parts": [
                    {"type": "thinking", "content": "long reasoning…"},
                    {"type": "text", "content": "answer"},
                ],
                "finish_reason": "stop",
            }
        ]
    )
    stripped = obs._strip_thinking_parts(value)
    assert isinstance(stripped, str)
    parsed = json.loads(stripped)
    assert parsed[0]["parts"] == [{"type": "text", "content": "answer"}]
    assert parsed[0]["finish_reason"] == "stop"


def test_strip_thinking_parts_preserves_clean_values_unchanged() -> None:
    clean = json.dumps(
        [{"role": "system", "parts": [{"type": "text", "content": "sys"}]}]
    )
    # Fast path: clean values are returned by identity without parsing.
    assert obs._strip_thinking_parts(clean) is clean


def test_strip_thinking_parts_fast_path_skips_non_thinking_strings() -> None:

    clean = (
        '[{"role":"system","parts":[{"type":"text","content":"no reasoning here"}]}]'
    )
    same = obs._strip_thinking_parts(clean)
    assert same is clean


def test_strip_thinking_parts_fast_path_does_not_skip_real_thinking() -> None:
    # "thinking" must appear literally in a message only as a thinking part;
    # verify the guard does not accidentally skip messages carrying it.
    with_thinking = '[{"role":"assistant","parts":[{"type":"thinking","content":"r"},{"type":"text","content":"t"}]}]'
    stripped = obs._strip_thinking_parts(with_thinking)
    assert json.loads(stripped)[0]["parts"] == [{"type": "text", "content": "t"}]


def test_strip_thinking_parts_tolerates_invalid_json() -> None:
    bad = "not-json-at-all"
    assert obs._strip_thinking_parts(bad) is bad


def test_strip_thinking_parts_invalid_json_with_thinking_sentinel() -> None:
    # Invalid JSON that still contains the literal substring must not crash;
    # json.loads raises and the original value is returned unchanged.
    bad = '{"role":"thinking"-not-json'
    assert obs._strip_thinking_parts(bad) is bad


def test_strip_thinking_parts_accepts_parsed_lists() -> None:
    value = [
        {
            "role": "assistant",
            "parts": [
                {"type": "thinking", "content": "r"},
                {"type": "text", "content": "t"},
            ],
        }
    ]
    stripped = obs._strip_thinking_parts(value)
    assert stripped == [
        {"role": "assistant", "parts": [{"type": "text", "content": "t"}]}
    ]


def test_mask_otel_spans_patches_thinking_attributes() -> None:
    import types as _types

    from langfuse.types import OtelSpanPatch  # noqa: F401

    messages = json.dumps(
        [
            {
                "role": "assistant",
                "parts": [
                    {"type": "thinking", "content": "r"},
                    {"type": "text", "content": "t"},
                ],
            }
        ]
    )

    span_attrs = {"gen_ai.output.messages": messages}
    params = _types.SimpleNamespace(
        spans={("0" * 32, "1" * 16): _types.SimpleNamespace(attributes=span_attrs)}
    )
    result = obs.mask_otel_spans(params=params)
    assert result is not None
    ident = ("0" * 32, "1" * 16)
    assert ident in result.span_patches
    patched = result.span_patches[ident]
    assert isinstance(patched, OtelSpanPatch)
    assert patched.set_attributes["gen_ai.output.messages"] == json.dumps(
        [{"role": "assistant", "parts": [{"type": "text", "content": "t"}]}]
    )


def test_mask_otel_spans_leaves_clean_spans_untouched() -> None:
    import types as _types

    messages = json.dumps(
        [{"role": "system", "parts": [{"type": "text", "content": "sys"}]}]
    )
    params = _types.SimpleNamespace(
        spans={
            ("2" * 32, "3" * 16): _types.SimpleNamespace(
                attributes={"gen_ai.input.messages": messages}
            )
        }
    )
    result = obs.mask_otel_spans(params=params)
    assert result is not None
    assert result.span_patches == {}


# --- email redaction ---------------------------------------------------------


def test_redact_emails_masks_string() -> None:
    assert (
        obs._redact_emails("contact user@example.com for details")
        == "contact [email redacted] for details"
    )


def test_redact_emails_masks_multiple_and_case_insensitive() -> None:
    redacted = obs._redact_emails("From A@B.IO to bob.smith+x@sub.example.co.uk")
    assert redacted == "From [email redacted] to [email redacted]"


def test_redact_emails_returns_same_string_when_no_match() -> None:
    clean = "no addresses here"
    assert obs._redact_emails(clean) is clean


def test_redact_emails_recurses_into_dicts_and_lists() -> None:
    value = {
        "role": "user",
        "parts": [
            {"type": "text", "content": "reach me at jane@example.org"},
            {"type": "text", "content": "plain"},
        ],
    }
    redacted = obs._redact_emails(value)
    assert redacted == {
        "role": "user",
        "parts": [
            {"type": "text", "content": "reach me at [email redacted]"},
            {"type": "text", "content": "plain"},
        ],
    }


def test_redact_emails_preserves_identity_when_nothing_matches() -> None:
    value = {"role": "user", "parts": [{"type": "text", "content": "plain"}]}
    assert obs._redact_emails(value) is value
    lst = [{"type": "text", "content": "plain"}]
    assert obs._redact_emails(lst) is lst


def test_redact_emails_leaves_non_containers_untouched() -> None:
    assert obs._redact_emails(42) == 42
    assert obs._redact_emails(None) is None


def test_strip_thinking_parts_redacts_emails_in_json_strings() -> None:
    value = json.dumps(
        [
            {
                "role": "user",
                "parts": [{"type": "text", "content": "email me at a@b.com"}],
            }
        ]
    )
    stripped = obs._strip_thinking_parts(value)
    assert isinstance(stripped, str)
    parsed = json.loads(stripped)
    assert parsed[0]["parts"][0]["content"] == "email me at [email redacted]"


def test_strip_thinking_parts_redacts_emails_in_parsed_lists() -> None:
    value = [{"role": "user", "parts": [{"type": "text", "content": "a@b.com"}]}]
    stripped = obs._strip_thinking_parts(value)
    assert stripped[0]["parts"][0]["content"] == "[email redacted]"


def test_mask_otel_spans_patches_email_attributes() -> None:
    import types as _types

    messages = json.dumps(
        [
            {
                "role": "user",
                "parts": [{"type": "text", "content": "contact a@b.com"}],
            }
        ]
    )
    params = _types.SimpleNamespace(
        spans={
            ("4" * 32, "5" * 16): _types.SimpleNamespace(
                attributes={"gen_ai.input.messages": messages}
            )
        }
    )
    result = obs.mask_otel_spans(params=params)
    assert result is not None
    ident = ("4" * 32, "5" * 16)
    assert ident in result.span_patches
    patched = result.span_patches[ident]
    assert patched.set_attributes["gen_ai.input.messages"] == json.dumps(
        [
            {
                "role": "user",
                "parts": [{"type": "text", "content": "contact [email redacted]"}],
            }
        ]
    )
