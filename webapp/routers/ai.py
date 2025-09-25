import os
from typing import Any, AsyncIterator, Iterable, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from webapp.ai import agent as get_agent
from webapp.ai import db as get_db
from webapp.ai import model as get_model
from webapp.utils.auth import require_ownership
from webapp.utils.logging import log_audit


# ===== Configuration (env-backed) =====

AI_MODEL_ID = os.getenv("AI_MODEL_ID", "openai/gpt-5-mini")
AI_REDIS_URL = os.getenv("AI_REDIS_URL", "redis://redis:6379")
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()

# Chat limits (mirror env, with safe defaults)
MAX_PAGES = int(os.getenv("AI_CHAT_MAX_PAGES", "5"))
MAX_CHARS_PER_PAGE = int(os.getenv("AI_CHAT_MAX_CHARS_PER_PAGE", "3000"))
MAX_TOTAL_CHARS = int(os.getenv("AI_CHAT_MAX_TOTAL_CHARS", "15000"))
MAX_MESSAGE_CHARS = int(os.getenv("AI_CHAT_MAX_MESSAGE_CHARS", "1000"))


# ===== Models =====

class ChatRequest(BaseModel):
    message: Any
    pages: Optional[List[Any]] = None


router = APIRouter(prefix="/api/ai")


# ===== Helpers =====

def _bool_env(name: str) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _audit(event: str, *, request: Optional[Request] = None, **fields: Any) -> None:
    try:
        log_audit(event, request=request, **fields)
    except Exception:
        # Never break control flow on logging failures
        pass


def _validate_and_normalize(body: ChatRequest, *, request: Request, user_id: str, analysis_id: str) -> Tuple[str, List[str], dict]:
    """
    Validate incoming body and apply truncation/budget logic.
    Returns: (compiled_prompt, selected_pages, stats) where stats is an audit-friendly dict.
    Raises: HTTPException(400) on validation errors.
    """
    # message
    if not isinstance(body.message, str) or not body.message.strip():
        _audit("ai_chat_validation_error", request=request, user_id=user_id, analysis_id=analysis_id, reason="missing_message")
        raise HTTPException(status_code=400, detail="message is required")
    msg = body.message.strip()
    if len(msg) > MAX_MESSAGE_CHARS:
        _audit(
            "ai_chat_limit_violation",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
            limit="message_chars",
            provided=len(msg),
            max=MAX_MESSAGE_CHARS,
        )
        raise HTTPException(status_code=400, detail="message too long")

    # pages
    pages_raw = body.pages if isinstance(body.pages, list) else None
    if pages_raw is None or any(not isinstance(p, str) for p in pages_raw):
        _audit("ai_chat_validation_error", request=request, user_id=user_id, analysis_id=analysis_id, reason="invalid_pages_type")
        raise HTTPException(status_code=400, detail="pages must be a list of strings")

    orig_pages_count = len(pages_raw)
    if orig_pages_count > MAX_PAGES:
        _audit(
            "ai_chat_limit_violation",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
            limit="pages",
            provided=orig_pages_count,
            max=MAX_PAGES,
        )
        raise HTTPException(status_code=400, detail=f"pages exceeds limit of {MAX_PAGES}")

    # Truncate to budgets (per-page and total)
    total_budget = MAX_TOTAL_CHARS
    selected: List[str] = []
    for s in pages_raw[:MAX_PAGES]:
        t = (s or "")[:MAX_CHARS_PER_PAGE]
        if total_budget <= 0:
            break
        if len(t) > total_budget:
            t = t[:total_budget]
        selected.append(t)
        total_budget -= len(t)

    # Build compiled prompt with context block
    context_parts: List[str] = []
    for i, txt in enumerate(selected, start=1):
        context_parts.append(f"### Page {i}\n\n{txt}")
    context_text = "\n\n".join(context_parts).strip()

    compiled_prompt = msg
    if context_text:
        compiled_prompt = f"{compiled_prompt}\n\nContext from selected pages:\n\n{context_text}"

    stats = {
        "pages_count": orig_pages_count,
        "selected_pages": len(selected),
        "total_chars": sum(len(p or "") for p in pages_raw),
        "selected_chars": sum(len(t) for t in selected),
        "truncated": bool((orig_pages_count > len(selected)) or (stats_chars(pages_raw) > sum(len(t) for t in selected))),
    }
    return compiled_prompt, selected, stats


def stats_chars(items: Iterable[str]) -> int:
    return sum(len(s or "") for s in items if isinstance(s, str))


def _preflight_config_guard(*, request: Request, user_id: str, analysis_id: str) -> Optional[StreamingResponse]:
    """
    Ensure we have minimal configuration for OpenRouter usage.
    Returns a StreamingResponse with a friendly error if misconfigured, else None.
    Skips guard when running tests (PYTEST_CURRENT_TEST is set).
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None

    model = str(AI_MODEL_ID or "").strip()

    # Basic key sanity (OpenRouter keys commonly start with "sk-")
    key_looks_bad = (not OPENROUTER_API_KEY) or (not OPENROUTER_API_KEY.startswith("sk-"))

    # Model sanity: require vendor/model and vendor is known
    allowed_vendors = {"openai", "anthropic", "mistral", "google", "meta", "perplexity", "cohere", "together"}
    parts = model.split("/", 1)
    invalid_model = (len(parts) != 2) or (parts[0].strip() not in allowed_vendors)

    if not key_looks_bad and not invalid_model:
        return None

    _audit(
        "ai_chat_config_error",
        request=request,
        user_id=user_id,
        analysis_id=analysis_id,
        reason=("missing_or_invalid_api_key" if key_looks_bad else "invalid_model_id"),
        model_id=AI_MODEL_ID,
    )

    async def _cfg_error_stream() -> AsyncIterator[str]:
        if key_looks_bad:
            yield "\n\n[Error] AI provider key missing or invalid. Please configure OPENROUTER_API_KEY."
        else:
            yield "\n\n[Error] AI model is not valid. Please configure a supported model (e.g., openai/gpt-4o-mini)."

    return StreamingResponse(_cfg_error_stream(), media_type="text/plain; charset=utf-8")


def _friendly_error(http_status: Optional[int], e: Exception) -> str:
    friendly = "\n\n[Error] Sorry, something went wrong. Please try again later."
    try:
        if http_status in (401, 403):
            friendly = "\n\n[Error] AI provider authentication failed. Please check configuration."
        elif http_status == 404:
            friendly = "\n\n[Error] AI model is unavailable. Please configure a valid model."
        elif http_status == 429:
            friendly = "\n\n[Error] Rate limited by AI provider. Please retry shortly."
        elif http_status and int(http_status) >= 500:
            friendly = "\n\n[Error] AI provider is currently unavailable. Please retry later."
    except Exception:
        pass

    # Optional verbose suffix for local dev
    if _bool_env("AI_CHAT_VERBOSE_ERRORS"):
        suffix = f" (code={type(e).__name__}"
        if http_status is not None:
            suffix += f", status={http_status}"
        suffix += ")"
        friendly += " " + suffix
    return friendly


def _make_agent(user_id: str, analysis_id: str):
    """Create model/db/agent instances."""
    model = get_model(model_id=AI_MODEL_ID, api_key=OPENROUTER_API_KEY)
    chat_db = get_db(redis_url=AI_REDIS_URL)
    return get_agent(user_id=user_id, session_id=analysis_id, model=model, db=chat_db)


async def _stream_agent(agent, compiled_prompt: str, *, request: Request, user_id: str, analysis_id: str) -> AsyncIterator[str]:
    """
    Uniform streaming wrapper for the provider agent.
    Yields text chunks. Any failure is logged + converted to a friendly message.
    """
    _audit("ai_chat_stream_begin", request=request, user_id=user_id, analysis_id=analysis_id)
    try:
        # arun may return an async generator, a coroutine, or a plain object
        resp: Any = agent.arun(compiled_prompt)

        # If coroutine, await to get the first materialized object
        try:
            import asyncio as _asyncio  # local import to avoid top-level coupling
            if _asyncio.iscoroutine(resp):
                resp = await resp
        except Exception:
            pass

        # Case 1: Async-iterable streaming
        if hasattr(resp, "__aiter__"):
            async for chunk in resp:  # type: ignore[func-returns-value]
                text = getattr(chunk, "content", None)
                if text is None:
                    try:
                        text = str(chunk)
                    except Exception:
                        text = ""
                if text:
                    yield str(text)
            return

        # Case 2: Single output object with .content
        if hasattr(resp, "content"):
            try:
                yield str(getattr(resp, "content"))
            except Exception:
                pass
            return

        # Case 3: Fallback to stringifying the response
        try:
            yield str(resp)
        except Exception:
            yield ""
    except Exception as e:
        # Extract an HTTP-like status if present
        http_status = None
        try:
            http_status = getattr(e, "status_code", None)
            if http_status is None:
                resp = getattr(e, "response", None)
                http_status = getattr(resp, "status_code", None)
        except Exception:
            http_status = None

        _audit(
            "ai_chat_error",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
            error_code=type(e).__name__,
            http_status=http_status,
            error_msg=str(e)[:500],
        )
        yield _friendly_error(http_status, e)
        return
    finally:
        _audit("ai_chat_stream_end", request=request, user_id=user_id, analysis_id=analysis_id)


# ===== Route =====

@router.post("/chat/{user_id}/{analysis_id}")
async def send_message(
    user_id: str,
    analysis_id: str,
    body: ChatRequest,
    request: Request,
) -> StreamingResponse:
    """
    Owners-only chat endpoint that streams plain-text model output.
    Enforces auth/ownership, validates payload, applies limits, then streams.
    """

    # Auth & ownership (owners-only)
    current_user = getattr(request.state, "current_user", None)
    if not current_user:
        _audit("ai_chat_unauthorized", request=request, user_id=user_id, analysis_id=analysis_id)
        raise HTTPException(status_code=401, detail="Unauthorized")
    if str(current_user.id) != str(user_id):
        _audit("ai_chat_forbidden", request=request, user_id=user_id, analysis_id=analysis_id, actor=str(current_user.id))
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        await require_ownership(request, analysis_id)
    except HTTPException as e:
        _audit("ai_chat_ownership_error", request=request, user_id=user_id, analysis_id=analysis_id, error_code=e.status_code)
        raise

    # Validate and normalize payload (compile prompt, select pages)
    compiled_prompt, selected_pages, stats = _validate_and_normalize(body, request=request, user_id=user_id, analysis_id=analysis_id)

    _audit(
        "ai_chat_start",
        request=request,
        user_id=user_id,
        analysis_id=analysis_id,
        model_id=AI_MODEL_ID,
        pages_count=stats["pages_count"],
        selected_pages=stats["selected_pages"],
        total_chars=stats["total_chars"],
        selected_chars=stats["selected_chars"],
        truncated=bool(stats["truncated"]),
    )

    # Optional dev stub for local testing (no provider)
    if _bool_env("AI_CHAT_DEV_STUB"):
        _audit("ai_chat_dev_stub", request=request, user_id=user_id, analysis_id=analysis_id)

        async def _dev_stub_stream() -> AsyncIterator[str]:
            yield "Stubbed AI response:\n\n"
            yield (compiled_prompt or "")[:2000]

        return StreamingResponse(_dev_stub_stream(), media_type="text/plain; charset=utf-8")

    # Preflight configuration checks (friendly message when misconfigured)
    maybe_resp = _preflight_config_guard(request=request, user_id=user_id, analysis_id=analysis_id)
    if maybe_resp is not None:
        return maybe_resp

    # Create agent and stream response
    agent = _make_agent(user_id=user_id, analysis_id=analysis_id)
    return StreamingResponse(
        _stream_agent(agent, compiled_prompt, request=request, user_id=user_id, analysis_id=analysis_id),
        media_type="text/plain; charset=utf-8",
    )
