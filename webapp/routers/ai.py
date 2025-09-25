import logging
import os
import json
from typing import Any, AsyncIterator, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from webapp.ai import agent as get_agent
from webapp.ai import db as get_db
from webapp.ai import model as get_model
from webapp.db import get_session
from webapp.models import ChatThread, ChatMessage
from webapp.utils.auth import require_ownership
from webapp.utils.logging import log_audit


AI_MODEL_ID = os.getenv("AI_MODEL_ID", "openai/gpt-5-mini")
AI_REDIS_URL = os.getenv("AI_REDIS_URL", "redis://redis:6379")
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()

# Chat limits (mirror env, with safe defaults)
MAX_PAGES = int(os.getenv("AI_CHAT_MAX_PAGES", "5"))
MAX_CHARS_PER_PAGE = int(os.getenv("AI_CHAT_MAX_CHARS_PER_PAGE", "3000"))
MAX_TOTAL_CHARS = int(os.getenv("AI_CHAT_MAX_TOTAL_CHARS", "15000"))
MAX_MESSAGE_CHARS = int(os.getenv("AI_CHAT_MAX_MESSAGE_CHARS", "1000"))


class ChatRequest(BaseModel):
    message: Any
    pages: Optional[List[Any]] = None


router = APIRouter(prefix="/api/ai")

logger = logging.getLogger(__name__)


def _ensure_thread(user_id: str, analysis_id: str) -> ChatThread:
    """Get or create a ChatThread row for this user+analysis."""
    with get_session() as s:
        thr = (
            s.query(ChatThread)
            .filter(ChatThread.user_id == user_id, ChatThread.crawl_id == analysis_id)
            .one_or_none()
        )
        if thr:
            return thr
        thr = ChatThread(user_id=user_id, crawl_id=analysis_id)
        s.add(thr)
        s.flush()
        return thr


def _bool_env(name: str) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _audit(event: str, *, request: Optional[Request] = None, **fields: Any) -> None:
    try:
        log_audit(event, request=request, **fields)
    except Exception:
        pass


def _friendly_error(http_status: Optional[int], e: Exception) -> str:
    friendly = "\n\n[Error] Sorry, something went wrong. Please try again later."
    try:
        if http_status in (401, 403):
            friendly = "\n\n[Error] AI provider authentication failed. Please check configuration."
        elif http_status == 404:
            friendly = (
                "\n\n[Error] AI model is unavailable. Please configure a valid model."
            )
        elif http_status == 429:
            friendly = "\n\n[Error] Rate limited by AI provider. Please retry shortly."
        elif http_status and int(http_status) >= 500:
            friendly = (
                "\n\n[Error] AI provider is currently unavailable. Please retry later."
            )
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


async def _stream_agent(
    agent, compiled_prompt: str, *, request: Request, user_id: str, analysis_id: str
) -> AsyncIterator[str]:
    """
    Uniform streaming wrapper for the provider agent.
    Yields text chunks. Any failure is logged + converted to a friendly message.
    """
    logger.info(
        f"Starting AI chat stream: user={user_id}, analysis={analysis_id}, model={AI_MODEL_ID}"
    )
    _audit(
        "ai_chat_stream_begin", request=request, user_id=user_id, analysis_id=analysis_id
    )
    try:
        resp = agent.arun(compiled_prompt)
        try:
            import asyncio
            if asyncio.iscoroutine(resp):
                resp = await resp
        except Exception:
            pass

        if hasattr(resp, "__aiter__"):
            async for chunk in resp:  # type: ignore[union-attr]
                text = getattr(chunk, "content", None)
                if text is None:
                    text = str(chunk)
                if text:
                    yield text
        elif hasattr(resp, "content"):
            text = str(getattr(resp, "content", ""))
            if text:
                yield text
        else:
            text = str(resp)
            if text:
                yield text
    except Exception as e:
        # Extract an HTTP-like status if present
        http_status = getattr(e, "status_code", None)
        if http_status is None:
            resp = getattr(e, "response", None)
            http_status = getattr(resp, "status_code", None)

        logger.exception(
            f"AI chat error: user={user_id}, analysis={analysis_id}, status={http_status}"
        )
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
    finally:
        _audit(
            "ai_chat_stream_end",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
        )


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
        _audit(
            "ai_chat_unauthorized",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
    if str(current_user.id) != str(user_id):
        _audit(
            "ai_chat_forbidden",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
            actor=str(current_user.id),
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        await require_ownership(request, analysis_id)
    except HTTPException as e:
        _audit(
            "ai_chat_ownership_error",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
            error_code=e.status_code,
        )
        raise

    # Validate message
    if not isinstance(body.message, str) or not body.message.strip():
        _audit(
            "ai_chat_validation_error",
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
            reason="missing_message",
        )
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

    # Validate pages
    pages = body.pages
    if pages is not None:
        if not isinstance(pages, list) or any(not isinstance(p, str) for p in pages):
            _audit(
                "ai_chat_validation_error",
                request=request,
                user_id=user_id,
                analysis_id=analysis_id,
                reason="invalid_pages_type",
            )
            raise HTTPException(status_code=400, detail="pages must be a list of strings")
        if len(pages) > MAX_PAGES:
            _audit(
                "ai_chat_limit_violation",
                request=request,
                user_id=user_id,
                analysis_id=analysis_id,
                limit="pages",
                provided=len(pages),
                max=MAX_PAGES,
            )
            raise HTTPException(
                status_code=400, detail=f"pages exceeds limit of {MAX_PAGES}"
            )

    # Truncate and compile prompt
    selected_pages = []
    total_chars = 0
    if pages:
        for p in pages[:MAX_PAGES]:
            truncated = (p or "")[:MAX_CHARS_PER_PAGE]
            if total_chars + len(truncated) > MAX_TOTAL_CHARS:
                truncated = truncated[: MAX_TOTAL_CHARS - total_chars]
            selected_pages.append(truncated)
            total_chars += len(truncated)
            if total_chars >= MAX_TOTAL_CHARS:
                break

    compiled_prompt = msg
    if selected_pages:
        context_parts = [f"### Page {i+1}\n\n{p}" for i, p in enumerate(selected_pages)]
        context = "\n\n".join(context_parts)
        compiled_prompt = f"{msg}\n\nContext from selected pages:\n\n{context}"

    logger.info(
        f"AI chat validated: user={user_id}, analysis={analysis_id}, pages_selected={len(selected_pages)}, total_chars={total_chars}"
    )
    _audit(
        "ai_chat_start",
        request=request,
        user_id=user_id,
        analysis_id=analysis_id,
        model_id=AI_MODEL_ID,
        pages_count=len(pages) if pages else 0,
        selected_pages=len(selected_pages),
        total_chars=sum(len(p or "") for p in (pages or [])),
        selected_chars=total_chars,
    )

    # Persist: ensure thread and save user message (with minimal metadata)
    try:
        thread = _ensure_thread(user_id=user_id, analysis_id=analysis_id)
        meta = {"pages_count": len(selected_pages), "selected_chars": total_chars}
        with get_session() as s:
            # Re-attach thread to this session when saving message
            th = s.query(ChatThread).filter(ChatThread.id == thread.id).one_or_none()
            if th:
                s.add(
                    ChatMessage(
                        thread_id=th.id,
                        role="user",
                        content=msg,
                        metadata_json=json.dumps(meta),
                    )
                )
    except Exception:
        logger.exception("Failed to persist user chat message")

    # Optional dev stub for local testing (no provider)
    if _bool_env("AI_CHAT_DEV_STUB"):
        _audit(
            "ai_chat_dev_stub", request=request, user_id=user_id, analysis_id=analysis_id
        )

        async def _dev_stub_stream() -> AsyncIterator[str]:
            yield "Stubbed AI response:\n\n"
            yield (compiled_prompt or "")[:2000]

        # Also persist the AI stubbed response after generation
        async def _proxy_dev_stream():
            buf = ""
            async for chunk in _dev_stub_stream():
                buf += chunk or ""
                yield chunk
            try:
                thread = _ensure_thread(user_id=user_id, analysis_id=analysis_id)
                with get_session() as s:
                    th = s.query(ChatThread).filter(ChatThread.id == thread.id).one_or_none()
                    if th:
                        s.add(ChatMessage(thread_id=th.id, role="ai", content=buf, metadata_json=None))
            except Exception:
                logger.exception("Failed to persist AI chat message (dev stub)")

        return StreamingResponse(
            _proxy_dev_stream(), media_type="text/plain; charset=utf-8"
        )

    # Create agent
    agent = _make_agent(user_id=user_id, analysis_id=analysis_id)

    # Proxy stream to both client and DB (persist AI message at the end)
    async def _proxy_stream_and_persist() -> AsyncIterator[str]:
        buf = ""
        async for chunk in _stream_agent(
            agent,
            compiled_prompt,
            request=request,
            user_id=user_id,
            analysis_id=analysis_id,
        ):
            text = chunk or ""
            buf += text
            yield text
        # Save AI message after streaming completes
        try:
            thread = _ensure_thread(user_id=user_id, analysis_id=analysis_id)
            with get_session() as s:
                th = s.query(ChatThread).filter(ChatThread.id == thread.id).one_or_none()
                if th:
                    s.add(
                        ChatMessage(
                            thread_id=th.id,
                            role="ai",
                            content=buf,
                            metadata_json=None,
                        )
                    )
        except Exception:
            logger.exception("Failed to persist AI chat message")

    return StreamingResponse(
        _proxy_stream_and_persist(), media_type="text/plain; charset=utf-8"
    )


@router.get("/chat/{user_id}/{analysis_id}/history")
async def get_chat_history(
    user_id: str, analysis_id: str, request: Request
) -> dict:
    """
    Return persisted chat history for this user+analysis (owner-only).
    """
    # Auth & ownership
    current_user = getattr(request.state, "current_user", None)
    if not current_user or str(current_user.id) != str(user_id):
        raise HTTPException(status_code=401 if not current_user else 403, detail="Unauthorized" if not current_user else "Forbidden")
    # Enforce ownership of the analysis/crawl
    await require_ownership(request, analysis_id)

    with get_session() as s:
        thr = (
            s.query(ChatThread)
            .filter(ChatThread.user_id == user_id, ChatThread.crawl_id == analysis_id)
            .one_or_none()
        )
        if not thr:
            return {"messages": []}
        rows = (
            s.query(ChatMessage)
            .filter(ChatMessage.thread_id == thr.id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        messages = []
        for m in rows:
            created_iso = None
            try:
                created_iso = (m.created_at.isoformat() if m.created_at else None)
            except Exception:
                created_iso = None
            messages.append(
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": created_iso,
                }
            )
        return {"messages": messages}


@router.delete("/chat/{user_id}/{analysis_id}")
async def clear_chat_history(
    user_id: str, analysis_id: str, request: Request
) -> dict:
    """
    Clear chat history for this user+analysis (owner-only).
    """
    # Auth & ownership
    current_user = getattr(request.state, "current_user", None)
    if not current_user or str(current_user.id) != str(user_id):
        raise HTTPException(status_code=401 if not current_user else 403, detail="Unauthorized" if not current_user else "Forbidden")
    await require_ownership(request, analysis_id)

    with get_session() as s:
        thr = (
            s.query(ChatThread)
            .filter(ChatThread.user_id == user_id, ChatThread.crawl_id == analysis_id)
            .one_or_none()
        )
        if thr:
            s.delete(thr)
    return {"status": "ok"}
