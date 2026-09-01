"""Observability wiring for pydantic-ai agents.

Exports LLM traces to Langfuse when credentials are present. The pydantic-ai
instrumentation emits OpenTelemetry spans through the global tracer provider;
instantiating the Langfuse client installs a Langfuse span processor onto that
provider, and ``Agent.instrument_all()`` routes every agent run through it.

The feature is opt-in: when ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
are unset this is a no-op, so local runs without credentials are unaffected.
Data residency / region is controlled by ``LANGFUSE_BASE_URL``; the default is
the EU cloud (``https://cloud.langfuse.com``), other regions include
``https://us.cloud.langfuse.com``, ``https://jp.cloud.langfuse.com`` and the
HIPAA instance ``https://hipaa.cloud.langfuse.com``. The client reads it from
the environment.

The Langfuse client is configured with a ``mask_otel_spans`` hook that strips
``thinking`` parts from the OpenTelemetry input/output attributes before they
reach Langfuse. Pydantic-ai (with ``include_content=True``, the default)
serializes the model's raw multi-kilobyte reasoning trace into
``gen_ai.input.messages`` / ``gen_ai.output.messages`` / ``pydantic_ai.all_messages``,
and Langfuse maps those onto the trace/observation inputs and outputs it
displays. Leaving the thinking blob in makes the UI's Input/Output panels
either render the ~44 KB reasoning dump or fail to parse it and show a blank
value. Since the injected attributes are the standard GenAI semantic
conventions, masking here is transparent to pydantic-ai itself.
"""

import atexit
import json
import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langfuse import Langfuse

logger = logging.getLogger(__name__)

# The configured Langfuse client, or None when tracing is disabled. Captured so
# callers can enrich traces (user_id / session_id / metadata / version) via
# ``trace_attributes`` below or the Langfuse SDK directly.
_client: Langfuse | None = None

# True once ``enable_langfuse`` has succeeded in this process. Guards the
# atexit flush handler against duplicate registration across repeated calls
# (multiple lifespans in one process, test suites, …).
_enabled = False

# Span attributes emitted by pydantic-ai whose serialized value is a JSON
# array of messages ({role, parts}) and may contain raw ``thinking`` parts.
# Strip those parts before export so Langfuse's UI shows a clean input/output
# instead of the multi-kilobyte reasoning trace.
_MESSAGE_ATTRIBUTES = frozenset(
    {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "pydantic_ai.all_messages",
        "gen_ai.system_instructions",
    }
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def _strip_thinking_parts(value: Any) -> Any:
    """Remove ``thinking`` parts from a JSON-encoded message array.

    Accepts either the JSON string pydantic-ai stores in span attributes or an
    already-parsed list/dict. Returns the input unchanged when nothing was
    stripped (so span attributes without reasoning content are never rewritten
    or re-serialized), and otherwise a value of the same shape as the input.
    """
    parsed = _parse_span_value(value)
    if parsed is _UNPARSED:
        return value
    if isinstance(parsed, list):
        cleaned = [_clean_message(m) for m in parsed]
        cleaned = [_redact_emails(m) for m in cleaned]
        if all(c is m for c, m in zip(cleaned, parsed)):
            return value
        return _encode_span_value(cleaned, value)
    if isinstance(parsed, dict):
        cleaned = _clean_message(parsed)
        cleaned = _redact_emails(cleaned)
        if cleaned is parsed:
            return value
        return _encode_span_value(cleaned, value)
    return value


# Sentinel: the value is not a JSON message container we can clean.
_UNPARSED = object()


def _parse_span_value(value: Any) -> Any:
    """Parsed container for a span attribute value, or _UNPARSED."""
    if isinstance(value, str):
        # Fast path: the serialized array only contains a thinking part
        # when the literal substring appears, so no-reasoning spans skip
        # the (multi-kilobyte) json.loads and tree traversal entirely.
        if "thinking" not in value and not _EMAIL_PATTERN.search(value):
            return _UNPARSED
        try:
            parsed = json.loads(value)
        except ValueError, TypeError:
            return _UNPARSED
        return parsed
    return value


def _encode_span_value(cleaned: Any, original: Any) -> Any:
    """Re-serialize when the input was a string, else return as-is."""
    return json.dumps(cleaned) if isinstance(original, str) else cleaned


def _clean_message(message: Any) -> Any:
    """Return ``message`` with ``type == "thinking"`` parts removed.

    Returns the original ``message`` object unchanged when no thinking part is
    present, so callers can detect that nothing changed by identity.
    """
    if not isinstance(message, dict):
        return message
    parts = message.get("parts")
    if not isinstance(parts, list):
        return message
    cleaned = [
        p for p in parts if not (isinstance(p, dict) and p.get("type") == "thinking")
    ]
    if len(cleaned) == len(parts):
        return message
    return {**message, "parts": cleaned}


def _redact_email_str(value: str) -> str:
    """Return ``value`` with email addresses masked (same object if clean)."""
    redacted = _EMAIL_PATTERN.sub("[email redacted]", value)
    return value if redacted == value else redacted


def _redact_email_list(value: list[Any]) -> list[Any]:
    """Redact each item, preserving identity when nothing changed."""
    redacted = [_redact_emails(item) for item in value]
    if all(a is b for a, b in zip(redacted, value)):
        return value
    return redacted


def _redact_email_dict(value: dict[Any, Any]) -> dict[Any, Any]:
    """Redact each value, preserving identity when nothing changed."""
    redacted = {key: _redact_emails(item) for key, item in value.items()}
    if all(redacted[key] is item for key, item in value.items()):
        return value
    return redacted


def _redact_emails(value: Any) -> Any:
    """Mask email addresses in exported LLM message content."""
    if isinstance(value, str):
        return _redact_email_str(value)
    if isinstance(value, list):
        return _redact_email_list(value)
    if isinstance(value, dict):
        return _redact_email_dict(value)
    return value


def mask_otel_spans(*, params: Any) -> Any:
    """Export-stage hook passed to the Langfuse client.

    Runs on every OpenTelemetry export batch inside the Langfuse span
    processor, immediately before the spans are turned into Langfuse
    observations. Rewrites the JSON-serialized message attributes so the raw
    reasoning ``thinking`` part does not reach Langfuse.

    The hook is a no-op for batches whose spans carry none of the message
    attributes, and returns ``None`` (drop the whole batch) only when the SDK
    convention calls for it — which we never do; we return an empty result,
    which the SDK treats as "leave the batch unchanged".
    """
    try:
        from langfuse.types import (
            MaskOtelSpansResult,
            OtelSpanPatch,
        )
    except ImportError:  # pragma: no cover - SDK versions without the hook
        return None

    span_patches = {}
    for identifier, params_span in params.spans.items():
        attributes = params_span.attributes
        set_attributes: dict[str, Any] = {}
        for key in _MESSAGE_ATTRIBUTES:
            value = attributes.get(key)
            if value is None:
                continue
            stripped = _strip_thinking_parts(value)
            if stripped is not value and stripped != value:
                set_attributes[key] = stripped
        if set_attributes:
            span_patches[identifier] = OtelSpanPatch(set_attributes=set_attributes)

    return MaskOtelSpansResult(span_patches=span_patches)


def enable_langfuse() -> bool:
    """Enable Langfuse tracing for pydantic-ai agents.

    Initialises the Langfuse client (which installs its OpenTelemetry span
    processor on the global tracer provider) and turns on pydantic-ai
    instrumentation for all agents created afterwards. Credentials are then
    verified with a best-effort ``auth_check``: a failure logs a warning but
    leaves tracing enabled, so a transient network error cannot silently
    disable it for the lifetime of a long-running process. A flush handler is
    registered so buffered spans are exported when the process exits, which
    matters for short-lived runs such as the CLI.

    Returns:
        bool: True if Langfuse tracing was enabled, False if credentials were
        missing, the SDK is not installed, or initialisation failed. On
        failure tracing is left disabled and the error is logged rather than
        raised, so observability never takes down the application that called
        this.
    """
    global _client, _enabled
    if _enabled:
        return True

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    base_url = os.getenv("LANGFUSE_BASE_URL", "").strip() or None
    if not public_key or not secret_key:
        return False

    try:
        from langfuse import Langfuse
        from pydantic_ai import Agent
    except ImportError as exc:
        logger.warning(
            "Skipping Langfuse tracing: %s (install the missing package to enable it)",
            exc,
        )
        return False

    try:
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            mask_otel_spans=mask_otel_spans,
        )
        Agent.instrument_all()
    except Exception:
        _client = None
        logger.exception("Failed to initialise Langfuse tracing; continuing without it")
        return False

    assert _client is not None
    _warn_if_auth_fails(_client)

    atexit.register(flush)
    _enabled = True
    logger.info("Langfuse tracing enabled for pydantic-ai agents")
    return True


def _warn_if_auth_fails(client: Langfuse) -> None:
    """Best-effort credential verification; never disables tracing.

    ``auth_check`` performs a blocking API call, so it is invoked exactly once
    per process, right after initialisation. A failure means exports will
    likely be rejected — surface it loudly instead of letting every span fail
    silently in the export background threads.
    """
    try:
        authenticated = client.auth_check()
    except Exception:
        logger.warning(
            "Langfuse auth check raised; check LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL — exports may fail",
            exc_info=True,
        )
        return
    if not authenticated:
        logger.warning(
            "Langfuse authentication failed; check LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL — exports may fail"
        )


def langfuse_client() -> Langfuse | None:
    """Return the configured Langfuse client, or None if tracing is disabled.

    Use the returned client to attach additional attributes to traces via the
    Langfuse SDK (``start_as_current_observation``, ``propagate_attributes``,
    ``@observe``).
    """
    return _client


@contextmanager
def trace_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    version: str | None = None,
) -> Iterator[None]:
    """Attach attributes to every Langfuse trace created within the block.

    Fail-soft wrapper around the Langfuse SDK's ``propagate_attributes``:
    a no-op when tracing is disabled (or the SDK is unavailable), so call
    sites never need to check whether Langfuse is configured. Typical use
    groups one analysis run's LLM traces into a single session:

        with trace_attributes(user_id=user, session_id=run_id, tags=["aax"]):
            ...
    """
    if _client is None:
        yield
        return

    try:
        from langfuse import propagate_attributes

        propagation = propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=tags,
            metadata=metadata,
            version=version,
        )
    except Exception:
        logger.exception(
            "Failed to start Langfuse attribute propagation; continuing without it"
        )
        yield
        return

    with propagation:
        yield


def flush() -> None:
    """Flush buffered Langfuse events to the API.

    Safe to call at the end of a short-lived process; it is a no-op when
    tracing is disabled. Registered with ``atexit`` by ``enable_langfuse``.
    The flush itself is guarded so a failure during interpreter shutdown (when
    the SDK's internals may already be tearing down) is logged rather than
    printed as an unhandled traceback.
    """
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            logger.exception("Failed to flush Langfuse events")
