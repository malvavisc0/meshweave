import types
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import webapp.routers.ai as ai


def create_test_app(user_id: str = "u1") -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request, call_next):
        # Simulate authenticated user in request.state.current_user
        request.state.current_user = SimpleNamespace(id=user_id)
        return await call_next(request)

    # Include the AI router under /api/ai/*
    app.include_router(ai.router)
    return app


@pytest.mark.parametrize(
    "body,expected_detail",
    [
        ({"message": "", "pages": ["md"]}, "message is required"),
        ({"message": "q", "pages": [1, {}]}, "pages must be a list of strings"),
    ],
)
def test_ai_chat_validation_errors(monkeypatch, body, expected_detail):
    # Stub ownership check to bypass DB
    async def _stub_require_ownership(request, analysis_id):
        return SimpleNamespace(id=analysis_id)

    monkeypatch.setattr(ai, "require_ownership", _stub_require_ownership, raising=True)

    # Do not let tests depend on agno; stub get_agent but it won't be reached on 400 paths
    class _Agent:
        async def arun(self, input):
            yield "unused"

    monkeypatch.setattr(ai, "get_agent", lambda **kwargs: _Agent(), raising=True)

    app = create_test_app(user_id="u1")
    client = TestClient(app)

    r = client.post("/api/ai/chat/u1/a1", json=body)
    assert r.status_code == 400
    j = r.json()
    assert "detail" in j
    assert j["detail"] == expected_detail


def test_ai_chat_pages_limit_violation(monkeypatch):
    # Stub ownership
    async def _stub_require_ownership(request, analysis_id):
        return SimpleNamespace(id=analysis_id)

    monkeypatch.setattr(ai, "require_ownership", _stub_require_ownership, raising=True)

    # Ensure limit = current module constant (default 5). Build 6 pages to trigger limit.
    over_pages = ["md"] * (ai.MAX_PAGES + 1)

    class _Agent:
        async def arun(self, input):
            yield "should not run on limit error"

    monkeypatch.setattr(ai, "get_agent", lambda **kwargs: _Agent(), raising=True)

    app = create_test_app(user_id="u1")
    client = TestClient(app)

    r = client.post("/api/ai/chat/u1/a1", json={"message": "q", "pages": over_pages})
    assert r.status_code == 400
    j = r.json()
    assert "detail" in j
    assert "exceeds limit" in j["detail"]


def test_ai_chat_message_length_cap(monkeypatch):
    # Apply optional message length cap
    monkeypatch.setattr(ai, "MAX_MESSAGE_CHARS", 5, raising=True)

    async def _stub_require_ownership(request, analysis_id):
        return SimpleNamespace(id=analysis_id)

    monkeypatch.setattr(ai, "require_ownership", _stub_require_ownership, raising=True)

    class _Agent:
        async def arun(self, input):
            yield "should not run on message cap"

    monkeypatch.setattr(ai, "get_agent", lambda **kwargs: _Agent(), raising=True)

    app = create_test_app(user_id="u1")
    client = TestClient(app)

    # 6 chars > 5 cap
    r = client.post("/api/ai/chat/u1/a1", json={"message": "123456", "pages": ["md"]})
    assert r.status_code == 400
    j = r.json()
    assert j.get("detail") == "message too long"


def test_ai_chat_streaming_success(monkeypatch):
    async def _stub_require_ownership(request, analysis_id):
        return SimpleNamespace(id=analysis_id)

    monkeypatch.setattr(ai, "require_ownership", _stub_require_ownership, raising=True)

    class _AgentOK:
        async def arun(self, input):
            async def _gen():
                yield "Hello "
                yield "world"
            return _gen()

    monkeypatch.setattr(ai, "get_agent", lambda **kwargs: _AgentOK(), raising=True)

    app = create_test_app(user_id="u1")
    client = TestClient(app)

    r = client.post("/api/ai/chat/u1/a1", json={"message": "q", "pages": ["md"]})
    assert r.status_code == 200
    # StreamingResponse is fully buffered by TestClient
    assert r.text == "Hello world"


def test_ai_chat_streaming_error_yields_friendly_note(monkeypatch):
    async def _stub_require_ownership(request, analysis_id):
        return SimpleNamespace(id=analysis_id)

    monkeypatch.setattr(ai, "require_ownership", _stub_require_ownership, raising=True)

    class _AgentErr:
        async def arun(self, input):
            raise RuntimeError("boom")

    monkeypatch.setattr(ai, "get_agent", lambda **kwargs: _AgentErr(), raising=True)

    app = create_test_app(user_id="u1")
    client = TestClient(app)

    r = client.post("/api/ai/chat/u1/a1", json={"message": "q", "pages": ["md"]})
    assert r.status_code == 200
    assert "[Error] Sorry, something went wrong. Please try again later." in r.text