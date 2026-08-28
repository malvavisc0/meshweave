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
"""

import atexit
import logging
import os
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
    if not public_key or not secret_key:
        return False

    try:
        from langfuse import get_client
        from pydantic_ai import Agent
    except ImportError as exc:
        logger.warning(
            "Skipping Langfuse tracing: %s (install the missing package to enable it)",
            exc,
        )
        return False

    try:
        _client = get_client()
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
